#!/bin/bash

# Copy Trading Bot - Background Start Script
# Runs the bot in background using screen or tmux (Linux and macOS compatible)

set -e  # Exit on any error

echo "========================================"
echo "COPY TRADING BOT - BACKGROUND STARTUP"
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

# Check if virtual environment exists
if [ ! -f "venv/bin/activate" ]; then
    print_error "Virtual environment not found!"
    
    # Detect OS and suggest appropriate install script
    if [[ "$OSTYPE" == "darwin"* ]]; then
        print_info "Please run ./install_macos.sh first"
    elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
        if command -v apt &> /dev/null; then
            print_info "Please run ./install_ubuntu.sh first"
        elif command -v yum &> /dev/null; then
            print_info "Please run ./install_centos.sh first"
        else
            print_info "Please run ./install_manual.sh first"
        fi
    else
        print_info "Please run the appropriate install script for your system first"
    fi
    exit 1
fi

# Check if .env file exists
if [ ! -f ".env" ]; then
    print_warning ".env file not found!"
    print_info "Please configure your .env file first"
    exit 1
fi

# Check if main.py exists
if [ ! -f "main.py" ]; then
    print_error "main.py not found!"
    exit 1
fi

# Check if bot is already running
if screen -list | grep -q "copy-trading-bot"; then
    print_warning "Bot is already running in background!"
    print_info "To stop: ./stop.sh"
    print_info "To view: screen -r copy-trading-bot"
    exit 1
fi

# Start in background using screen
if command -v screen &> /dev/null; then
    print_info "Starting bot in background using screen..."
    screen -dmS copy-trading-bot bash -c "source venv/bin/activate && python main.py"
    sleep 2
    
    if screen -list | grep -q "copy-trading-bot"; then
        print_success "Bot started successfully in background!"
        echo
        print_info "To view the bot output:"
        print_info "screen -r copy-trading-bot"
        echo
        print_info "To detach from screen (keep bot running):"
        print_info "Press Ctrl+A then D"
        echo
        print_info "To stop the bot:"
        print_info "./stop.sh"
        echo
        print_info "Dashboard: http://localhost:5000"
        print_info "API: http://localhost:8000"
    else
        print_error "Failed to start bot in background"
        exit 1
    fi

elif command -v tmux &> /dev/null; then
    print_info "Starting bot in background using tmux..."
    tmux new-session -d -s copy-trading-bot "source venv/bin/activate && python main.py"
    sleep 2
    
    if tmux list-sessions | grep -q "copy-trading-bot"; then
        print_success "Bot started successfully in background!"
        echo
        print_info "To view the bot output:"
        print_info "tmux attach-session -t copy-trading-bot"
        echo
        print_info "To detach from tmux (keep bot running):"
        print_info "Press Ctrl+B then D"
        echo
        print_info "To stop the bot:"
        print_info "./stop.sh"
        echo
        print_info "Dashboard: http://localhost:5000"
        print_info "API: http://localhost:8000"
    else
        print_error "Failed to start bot in background"
        exit 1
    fi

else
    print_error "Neither screen nor tmux found!"
    
    # Detect OS and suggest installation method
    if [[ "$OSTYPE" == "darwin"* ]]; then
        print_info "Installing screen via Homebrew..."
        if command -v brew &> /dev/null; then
            brew install screen
            print_info "Please run this script again"
        else
            print_error "Homebrew not found. Please install screen manually:"
            print_info "brew install screen"
        fi
    elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
        if command -v apt &> /dev/null; then
            print_info "Installing screen..."
            sudo apt install -y screen
            print_info "Please run this script again"
        elif command -v yum &> /dev/null; then
            print_info "Installing screen..."
            sudo yum install -y screen
            print_info "Please run this script again"
        else
            print_error "Please install screen or tmux manually"
        fi
    else
        print_error "Please install screen or tmux manually for your system"
    fi
    exit 1
fi
