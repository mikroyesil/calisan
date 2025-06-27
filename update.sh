#!/bin/bash
# OTA Update Script for Vertical Farm Control System
# Auto-detects the correct directory

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$SCRIPT_DIR"
LOG_FILE="$APP_DIR/update.log"
BACKUP_DIR="$(dirname "$APP_DIR")/calisan-backup"
SERVICE_NAME="vertical-farm"

echo "🔍 Auto-detected directories:"
echo "   Script location: $SCRIPT_DIR"
echo "   App directory: $APP_DIR"
echo "   Log file: $LOG_FILE"
echo "   Backup directory: $BACKUP_DIR"
echo ""

# Create log file if it doesn't exist
touch "$LOG_FILE" 2>/dev/null || {
    echo "⚠️  Warning: Cannot create log file at $LOG_FILE"
    LOG_FILE="/tmp/vertical-farm-update.log"
    echo "   Using temporary log file: $LOG_FILE"
    touch "$LOG_FILE"
}

echo "$(date): Starting OTA update..." >> "$LOG_FILE"

# Function to log messages
log_message() {
    echo "$(date): $1" | tee -a "$LOG_FILE"
}

# Function to safely handle uncommitted changes
handle_uncommitted_changes() {
    log_message "🔍 Checking for uncommitted changes..."
    
    # Check if there are any changes
    if git diff-index --quiet HEAD -- 2>/dev/null; then
        log_message "✅ No uncommitted changes detected"
        return 0
    fi
    
    log_message "⚠️  Uncommitted changes detected!"
    
    # Show what files are modified
    git status --porcelain | while read line; do
        log_message "   Changed: $line"
    done
    
    # Create a backup of current changes
    BACKUP_BRANCH="backup-$(date +%Y%m%d-%H%M%S)"
    log_message "💾 Creating backup branch: $BACKUP_BRANCH"
    
    # Add all changes and create backup branch
    git add . 2>&1 | while read line; do log_message "   git add: $line"; done
    git commit -m "Backup before update $(date)" 2>&1 | while read line; do log_message "   git commit: $line"; done
    git branch "$BACKUP_BRANCH" 2>&1 | while read line; do log_message "   git branch: $line"; done
    
    log_message "✅ Backup created on branch: $BACKUP_BRANCH"
    log_message "ℹ️  To restore changes later: git checkout $BACKUP_BRANCH"
    
    return 0
}

# Check current directory
log_message "Current working directory: $(pwd)"
log_message "App directory: $APP_DIR"

# Check if we're in the right place
if [ ! -f "$APP_DIR/app.py" ]; then
    log_message "⚠️  Warning: app.py not found in $APP_DIR"
    log_message "Directory contents:"
    ls -la "$APP_DIR" | while read line; do log_message "   $line"; done
fi

# Simple git update
log_message "=== Starting Git Update ==="
cd "$APP_DIR"

# Check git status
if [ -d ".git" ]; then
    log_message "✅ Git repository found"
    
    # Check git status
    log_message "🔍 Checking git repository status..."
    git status --porcelain 2>&1 | while read line; do
        if [ -n "$line" ]; then
            log_message "   Modified: $line"
        fi
    done
    
    # Check if we have a remote
    if ! git remote get-url origin >/dev/null 2>&1; then
        log_message "⚠️  No remote origin found, adding it..."
        git remote add origin https://github.com/mikroyesil/calisan.git
        log_message "✅ Remote origin added"
    else
        log_message "✅ Remote origin configured: $(git remote get-url origin)"
    fi
else
    log_message "❌ Not a git repository, initializing..."
    git init 2>&1 | while read line; do log_message "   git init: $line"; done
    git remote add origin https://github.com/mikroyesil/calisan.git
    log_message "✅ Git repository initialized"
fi

# Handle uncommitted changes safely
handle_uncommitted_changes

# Pull updates
log_message "⬇️  Pulling updates from GitHub..."

# Configure git to handle divergent branches automatically
git config pull.rebase false 2>&1 | while read line; do log_message "   git config: $line"; done

# Capture git pull output and check for success
PULL_OUTPUT=$(mktemp)
if git pull origin main --allow-unrelated-histories 2>&1 | tee "$PULL_OUTPUT" | while read line; do log_message "   git pull: $line"; done; then
    # Check if there were any fatal errors in the output
    if grep -q "fatal:" "$PULL_OUTPUT"; then
        log_message "⚠️  Git pull had fatal errors, trying alternative method..."
        rm -f "$PULL_OUTPUT"
        
        # Try fetching and resetting to ensure we get the latest code
        log_message "📥 Fetching latest changes..."
        if git fetch origin main 2>&1 | while read line; do log_message "   git fetch: $line"; done; then
            log_message "✅ Fetch successful"
            
            # Force update to match remote exactly
            log_message "🔄 Resetting to match remote repository..."
            git reset --hard origin/main 2>&1 | while read line; do log_message "   git reset: $line"; done
            log_message "✅ Repository updated to match remote"
        else
            log_message "❌ Fetch also failed - checking network connectivity..."
            
            # Test network connectivity
            if ping -c 1 github.com >/dev/null 2>&1; then
                log_message "✅ Network connectivity OK"
                log_message "❌ Repository access issue - check credentials or repository URL"
            else
                log_message "❌ Network connectivity issue"
            fi
        fi
    else
        log_message "✅ Git pull successful"
        rm -f "$PULL_OUTPUT"
    fi
else
    rm -f "$PULL_OUTPUT"
    log_message "⚠️  Git pull command failed, trying alternative method..."
    
    # Try fetching and resetting to ensure we get the latest code
    log_message "📥 Fetching latest changes..."
    if git fetch origin main 2>&1 | while read line; do log_message "   git fetch: $line"; done; then
        log_message "✅ Fetch successful"
        
        # Force update to match remote exactly
        log_message "🔄 Resetting to match remote repository..."
        git reset --hard origin/main 2>&1 | while read line; do log_message "   git reset: $line"; done
        log_message "✅ Repository updated to match remote"
    else
        log_message "❌ Fetch also failed - checking network connectivity..."
        
        # Test network connectivity
        if ping -c 1 github.com >/dev/null 2>&1; then
            log_message "✅ Network connectivity OK"
            log_message "❌ Repository access issue - check credentials or repository URL"
        else
            log_message "❌ Network connectivity issue"
        fi
    fi
fi

# Update Python packages if venv exists
if [ -d "venv" ]; then
    log_message "🐍 Updating Python packages..."
    source venv/bin/activate
    pip install -r requirements.txt --upgrade 2>/dev/null
    log_message "✅ Python packages updated"
else
    log_message "⚠️  Virtual environment not found, skipping pip install"
fi

# Restart service if it exists
if systemctl list-unit-files | grep -q "$SERVICE_NAME.service"; then
    log_message "🔄 Restarting service..."
    sudo systemctl restart "$SERVICE_NAME"
    sleep 3
    if systemctl is-active --quiet "$SERVICE_NAME"; then
        log_message "✅ Service restarted successfully"
    else
        log_message "⚠️  Service may not be running properly"
    fi
else
    log_message "ℹ️  Service $SERVICE_NAME not found, skipping restart"
fi

log_message "🎉 Update completed!"
echo ""
echo "✅ Update finished! Check the log file for details:"
echo "   $LOG_FILE"
