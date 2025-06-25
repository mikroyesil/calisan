from setuptools import setup, find_packages

setup(
    name="verticalkontrol",
    version="0.1",
    packages=find_packages(),
    install_requires=[
        'flask',
        'flask-socketio',
        'numpy',
    ],
    python_requires='>=3.7',
)