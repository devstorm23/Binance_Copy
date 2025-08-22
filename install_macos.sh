#!/bin/bash

# Copy Trading Bot - macOS Installation Script
# This script sets up the complete environment for the copy trading bot on macOS

set -e  # Exit on any error

echo "========================================"
echo "COPY TRADING BOT - MACOS INSTALLATION"
echo "========================================"
echo

# Function to print colored output
print_info() {
    echo -e "\033[1;34m[INFO]\033[0m $1"
}

print_success() {
    echo -e "\033[1;32m[SUCCESS]\033[0m $1"
}

print_warning() {
    echo -e "\033[1;33m[WARNING]\033[0m $1"
}

print_error() {
    echo -e "\033[1;31m[ERROR]\033[0m $1"
}

# Check if running as root
if [ "$EUID" -eq 0 ]; then
    print_warning "Please do not run this script as root"
    print_info "Run: ./install_macos.sh"
    exit 1
fi

# Check if we're on macOS
if [[ "$OSTYPE" != "darwin"* ]]; then
    print_error "This script is designed for macOS only"
    print_info "For other systems, use:"
    print_info "  - Ubuntu/Debian: ./install_ubuntu.sh"
    print_info "  - Manual install: ./install_manual.sh"
    exit 1
fi

print_info "Detected macOS system"

# Check if Homebrew is installed
if ! command -v brew &> /dev/null; then
    print_warning "Homebrew not found. Installing Homebrew..."
    print_info "This will install Homebrew (the package manager for macOS)"
    read -p "Continue? [Y/n]: " -r
    if [[ $REPLY =~ ^[Nn]$ ]]; then
        print_error "Homebrew is required for this installation"
        print_info "You can install it manually from: https://brew.sh"
        print_info "Or use the manual installation script: ./install_manual.sh"
        exit 1
    fi
    
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    
    # Add Homebrew to PATH for the current session
    if [[ -f "/opt/homebrew/bin/brew" ]]; then
        # Apple Silicon Mac
        eval "$(/opt/homebrew/bin/brew shellenv)"
        echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile
    elif [[ -f "/usr/local/bin/brew" ]]; then
        # Intel Mac
        eval "$(/usr/local/bin/brew shellenv)"
        echo 'eval "$(/usr/local/bin/brew shellenv)"' >> ~/.zprofile
    fi
    
    print_success "Homebrew installed successfully"
else
    print_success "Homebrew found"
fi

# Update Homebrew
print_info "Updating Homebrew..."
brew update

# Check Python version
if command -v python3 &> /dev/null; then
    python_version=$(python3 --version 2>/dev/null | cut -d' ' -f2 | cut -d'.' -f1,2)
    print_info "Found Python $python_version"
else
    python_version=""
fi

required_version="3.8"
if [ -z "$python_version" ] || [ "$(printf '%s\n' "$required_version" "$python_version" | sort -V | head -n1)" != "$required_version" ]; then
    print_warning "Python 3.8+ not found or incompatible version"
    print_info "Installing Python via Homebrew..."
    brew install python@3.11
    
    # Add Python to PATH
    if [[ -d "/opt/homebrew/bin" ]]; then
        # Apple Silicon Mac
        export PATH="/opt/homebrew/bin:$PATH"
        echo 'export PATH="/opt/homebrew/bin:$PATH"' >> ~/.zprofile
    else
        # Intel Mac
        export PATH="/usr/local/bin:$PATH"
        echo 'export PATH="/usr/local/bin:$PATH"' >> ~/.zprofile
    fi
    
    print_success "Python installed successfully"
else
    print_success "Python $python_version is compatible"
fi

# Install system dependencies via Homebrew
print_info "Installing system dependencies..."
brew install \
    git \
    curl \
    wget \
    screen \
    tmux \
    openssl \
    libffi

# Install optional but useful tools
print_info "Installing additional useful tools..."
brew install \
    htop \
    nano \
    tree

# Create virtual environment
print_info "Creating Python virtual environment..."
if [ -d "venv" ]; then
    print_warning "Virtual environment already exists, removing old one..."
    rm -rf venv
fi

python3 -m venv venv
print_success "Virtual environment created"

# Activate virtual environment
print_info "Activating virtual environment..."
source venv/bin/activate

# Upgrade pip
print_info "Upgrading pip..."
pip install --upgrade pip setuptools wheel

# Install Python dependencies
print_info "Installing Python dependencies..."
if [ -f "requirements.txt" ]; then
    # On macOS, we might need to set some environment variables for certain packages
    export LDFLAGS="-L$(brew --prefix openssl)/lib"
    export CPPFLAGS="-I$(brew --prefix openssl)/include"
    export PKG_CONFIG_PATH="$(brew --prefix openssl)/lib/pkgconfig"
    
    pip install -r requirements.txt
    print_success "Dependencies installed from requirements.txt"
else
    print_warning "requirements.txt not found, installing basic dependencies..."
    
    # Set OpenSSL flags for cryptography package
    export LDFLAGS="-L$(brew --prefix openssl)/lib"
    export CPPFLAGS="-I$(brew --prefix openssl)/include"
    export PKG_CONFIG_PATH="$(brew --prefix openssl)/lib/pkgconfig"
    
    pip install \
        python-binance==1.0.19 \
        ccxt==4.1.77 \
        fastapi==0.111.0 \
        uvicorn==0.24.0 \
        python-multipart==0.0.7 \
        python-jose[cryptography]==3.3.0 \
        passlib[bcrypt]==1.7.4 \
        python-dotenv==1.0.0 \
        websockets==10.4 \
        aiohttp==3.9.5 \
        pandas==2.1.4 \
        numpy==1.25.2 \
        pydantic==2.7.0 \
        sqlalchemy==2.0.23 \
        alembic==1.13.1 \
        psycopg2-binary==2.9.9 \
        redis==5.0.1 \
        celery==5.3.4 \
        flask==3.0.0 \
        flask-cors==4.0.0 \
        flask-socketio==5.3.6 \
        eventlet==0.33.3 \
        cryptography==3.4.8 \
        httpx==0.23.0 \
        requests==2.32.1 \
        pyOpenSSL==23.3.0 \
        nest-asyncio==1.5.8
fi

# Create .env file if it doesn't exist
print_info "Setting up environment configuration..."
if [ ! -f ".env" ]; then
    if [ -f "env_example.txt" ]; then
        cp env_example.txt .env
        print_warning ".env file created from env_example.txt"
        print_warning "Please edit .env file with your API keys and settings"
    else
        cat > .env << EOF
# Copy Trading Bot Configuration
# Edit these values with your actual API keys and settings

# Database Configuration
DATABASE_URL=sqlite:///./copy_trading.db

# API Security
API_SECRET_KEY=your-secret-key-here-change-this
API_TOKEN=butter1011

# Binance Configuration
BINANCE_TESTNET=True
# Set to False for live trading (BE CAREFUL!)

# Logging
LOG_LEVEL=INFO

# Server Configuration
API_HOST=0.0.0.0
API_PORT=8000
DASHBOARD_HOST=0.0.0.0
DASHBOARD_PORT=5000

# Risk Management (Optional - can be set per account)
DEFAULT_RISK_PERCENTAGE=5.0
DEFAULT_LEVERAGE=10
EOF
        print_warning ".env file created with default values"
        print_warning "Please edit .env file with your actual API keys and settings"
    fi
else
    print_success ".env file already exists"
fi

# Set file permissions
print_info "Setting file permissions..."
chmod +x *.sh 2>/dev/null || true
chmod 600 .env 2>/dev/null || true

# Create log directory
print_info "Creating log directory..."
mkdir -p logs
chmod 755 logs

# Initialize database (if main.py exists)
if [ -f "main.py" ]; then
    print_info "Initializing database..."
    python main.py --init-db 2>/dev/null || print_warning "Database initialization skipped (normal if already exists)"
fi

# Create LaunchAgent for macOS (similar to systemd on Linux)
print_info "Creating macOS LaunchAgent..."
LAUNCH_AGENT_DIR="$HOME/Library/LaunchAgents"
mkdir -p "$LAUNCH_AGENT_DIR"

cat > "$LAUNCH_AGENT_DIR/com.copytrading.bot.plist" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.copytrading.bot</string>
    <key>ProgramArguments</key>
    <array>
        <string>$(pwd)/venv/bin/python</string>
        <string>$(pwd)/main.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$(pwd)</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$(pwd)/logs/bot.out.log</string>
    <key>StandardErrorPath</key>
    <string>$(pwd)/logs/bot.err.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>$(pwd)/venv/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>
</dict>
</plist>
EOF

print_info "To install as macOS LaunchAgent (auto-start), run:"
print_info "launchctl load ~/Library/LaunchAgents/com.copytrading.bot.plist"
print_info "launchctl start com.copytrading.bot"
print_info ""
print_info "To uninstall the LaunchAgent:"
print_info "launchctl stop com.copytrading.bot"
print_info "launchctl unload ~/Library/LaunchAgents/com.copytrading.bot.plist"

print_success "Installation completed successfully!"
echo
echo "========================================"
echo "NEXT STEPS:"
echo "========================================"
echo "1. Edit .env file with your API keys:"
echo "   nano .env"
echo
echo "2. Start the bot:"
echo "   ./start.sh"
echo
echo "3. Or run in background:"
echo "   ./start_background.sh"
echo
echo "4. Access the dashboard:"
echo "   http://localhost:5000"
echo
echo "5. Access the API:"
echo "   http://localhost:8000"
echo
echo "========================================"
echo "MACOS SPECIFIC NOTES:"
echo "========================================"
echo "- Python and dependencies installed via Homebrew"
echo "- LaunchAgent created for auto-start capability"
echo "- All shell scripts are macOS compatible"
echo "- OpenSSL configured for cryptography packages"
echo "========================================"
echo "IMPORTANT SECURITY NOTES:"
echo "========================================"
echo "- Edit .env file with your actual API keys"
echo "- Set BINANCE_TESTNET=False only when ready for live trading"
echo "- Never share your .env file or API keys"
echo "- Start with small amounts for testing"
echo "========================================"
