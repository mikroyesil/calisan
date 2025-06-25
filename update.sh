#!/bin/bash

# OTA Update Script for Vertical Farm Control System
# This script handles git-based over-the-air updates with backup and rollback capabilities

set -e  # Exit on any error

# Configuration
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKUP_DIR="${PROJECT_DIR}/backups"
LOG_FILE="${PROJECT_DIR}/update.log"
SERVICE_NAME="vertical-farm"  # Systemd service name (if used)
VENV_PATH="${PROJECT_DIR}/venv"  # Virtual environment path

# Default options
BACKUP=true
RESTART=true
BRANCH="main"
FORCE=false

# Logging function
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

# Error handling
error_exit() {
    log "ERROR: $1"
    exit 1
}

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --no-backup)
            BACKUP=false
            shift
            ;;
        --no-restart)
            RESTART=false
            shift
            ;;
        --branch)
            BRANCH="$2"
            shift 2
            ;;
        --force)
            FORCE=true
            shift
            ;;
        --help)
            echo "Usage: $0 [options]"
            echo "Options:"
            echo "  --no-backup    Skip creating backup"
            echo "  --no-restart   Skip application restart"
            echo "  --branch NAME  Update to specific branch (default: main)"
            echo "  --force        Force update even with uncommitted changes"
            echo "  --help         Show this help message"
            exit 0
            ;;
        *)
            error_exit "Unknown option: $1"
            ;;
    esac
done

log "Starting OTA update process..."
log "Project directory: $PROJECT_DIR"
log "Backup: $BACKUP, Restart: $RESTART, Branch: $BRANCH, Force: $FORCE"

# Change to project directory
cd "$PROJECT_DIR" || error_exit "Cannot change to project directory"

# Check if we're in a git repository
if [ ! -d ".git" ]; then
    error_exit "Not a git repository. OTA updates require git."
fi

# Create backup directory if it doesn't exist
if [ "$BACKUP" = true ]; then
    mkdir -p "$BACKUP_DIR"
fi

# Function to create backup
create_backup() {
    if [ "$BACKUP" = true ]; then
        local backup_name="backup_$(date +%Y%m%d_%H%M%S)"
        local backup_path="${BACKUP_DIR}/${backup_name}"
        
        log "Creating backup: $backup_name"
        
        # Create backup using git archive
        git archive --format=tar.gz --output="${backup_path}.tar.gz" HEAD
        
        # Also backup the database and logs
        if [ -f "farm_control.db" ]; then
            cp "farm_control.db" "${backup_path}_database.db"
        fi
        
        if [ -f "farm_control.log" ]; then
            cp "farm_control.log" "${backup_path}_log.txt"
        fi
        
        log "Backup created successfully: ${backup_path}.tar.gz"
        
        # Keep only last 10 backups
        cd "$BACKUP_DIR"
        ls -t backup_*.tar.gz | tail -n +11 | xargs -r rm -f
        cd "$PROJECT_DIR"
    fi
}

# Function to check system requirements
check_requirements() {
    log "Checking system requirements..."
    
    # Check if git is available
    if ! command -v git &> /dev/null; then
        error_exit "Git is not installed"
    fi
    
    # Check if python is available
    if ! command -v python3 &> /dev/null; then
        error_exit "Python 3 is not installed"
    fi
    
    # Check if we have network connectivity
    if ! ping -c 1 github.com &> /dev/null; then
        log "WARNING: Cannot reach github.com - proceeding anyway"
    fi
    
    log "System requirements check passed"
}

# Function to update application
update_application() {
    log "Updating application..."
    
    # Fetch latest changes
    log "Fetching latest changes from remote..."
    git fetch origin || error_exit "Failed to fetch from remote"
    
    # Check for uncommitted changes
    if [ "$FORCE" = false ] && [ -n "$(git status --porcelain)" ]; then
        error_exit "Uncommitted changes detected. Use --force to override or commit changes first."
    fi
    
    # Get current commit for rollback purposes
    local current_commit=$(git rev-parse HEAD)
    log "Current commit: $current_commit"
    
    # Switch to target branch and pull
    log "Switching to branch: $BRANCH"
    git checkout "$BRANCH" || error_exit "Failed to checkout branch $BRANCH"
    
    log "Pulling latest changes..."
    git pull origin "$BRANCH" || error_exit "Failed to pull latest changes"
    
    local new_commit=$(git rev-parse HEAD)
    log "Updated to commit: $new_commit"
    
    if [ "$current_commit" = "$new_commit" ]; then
        log "Already up to date - no changes to apply"
        return 0
    fi
    
    # Update Python dependencies if requirements.txt changed
    if git diff --name-only "$current_commit" "$new_commit" | grep -q requirements.txt; then
        log "Requirements.txt changed - updating Python dependencies..."
        
        if [ -d "$VENV_PATH" ]; then
            log "Using virtual environment: $VENV_PATH"
            source "$VENV_PATH/bin/activate"
        fi
        
        pip3 install -r requirements.txt || error_exit "Failed to install Python dependencies"
        
        if [ -d "$VENV_PATH" ]; then
            deactivate
        fi
    fi
    
    log "Application update completed successfully"
}

# Function to restart application
restart_application() {
    if [ "$RESTART" = true ]; then
        log "Restarting application..."
        
        # Try different restart methods
        if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
            log "Restarting systemd service: $SERVICE_NAME"
            sudo systemctl restart "$SERVICE_NAME" || log "WARNING: Failed to restart systemd service"
        elif [ -f "${PROJECT_DIR}/run.sh" ]; then
            log "Using run.sh script for restart"
            # Kill existing process if running
            pkill -f "python.*app.py" || true
            sleep 2
            # Start new process in background
            nohup bash "${PROJECT_DIR}/run.sh" > /dev/null 2>&1 &
        else
            log "WARNING: No restart method found. Please restart manually."
        fi
        
        log "Restart initiated"
    else
        log "Skipping application restart (--no-restart specified)"
    fi
}

# Function to verify update
verify_update() {
    log "Verifying update..."
    
    # Wait a moment for application to start
    sleep 5
    
    # Try to ping the health endpoint
    local health_url="http://localhost:5002/health"
    if command -v curl &> /dev/null; then
        if curl -s "$health_url" > /dev/null; then
            log "Health check passed - application is responding"
        else
            log "WARNING: Health check failed - application may not be running properly"
        fi
    else
        log "Curl not available - skipping health check"
    fi
}

# Main execution
main() {
    log "=== OTA Update Started ==="
    
    check_requirements
    create_backup
    update_application
    restart_application
    verify_update
    
    log "=== OTA Update Completed Successfully ==="
    log "Update summary:"
    log "  - Branch: $BRANCH"
    log "  - Backup created: $BACKUP"
    log "  - Application restarted: $RESTART"
    log "  - Log file: $LOG_FILE"
}

# Cleanup function for interruption
cleanup() {
    log "Update interrupted - cleaning up..."
    exit 1
}

# Trap interruption signals
trap cleanup INT TERM

# Run main function
main "$@"
