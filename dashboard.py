import os
import sys

# Set environment variable to avoid OpenSSL issues
os.environ['OPENSSL_CONF'] = ''

from flask import Flask, render_template, request, jsonify, redirect, url_for, flash
from flask_socketio import SocketIO, emit
import requests
import json
import threading
import time
from datetime import datetime
import logging

app = Flask(__name__)
app.config['SECRET_KEY'] = 'butter1011'
# Use threading async mode to avoid conflicts with asyncio/uvicorn in the same process
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# API configuration
API_BASE_URL = "http://127.0.0.1:8000"  # Use IP instead of localhost to avoid DNS issues
API_TOKEN = "butter1011"

# Global variables for real-time updates
system_status = {}
accounts_data = []
trades_data = []
logs_data = []
copy_configs_data = []

def fetch_api_data(endpoint, params=None):
    """Fetch data from the API"""
    try:
        headers = {"Authorization": f"Bearer {API_TOKEN}"}
        response = requests.get(f"{API_BASE_URL}{endpoint}", headers=headers, params=params, timeout=15)
        if response.status_code == 200:
            return response.json()
        else:
            logging.error(f"API request failed with status {response.status_code} for endpoint {endpoint}")
            return None
    except requests.exceptions.ConnectionError as e:
        logging.error(f"API connection failed: Cannot connect to {API_BASE_URL}{endpoint} - {e}")
        return None
    except requests.exceptions.Timeout as e:
        logging.error(f"API request timeout: {endpoint} - {e}")
        return None
    except requests.exceptions.RequestException as e:
        logging.error(f"API request failed for {endpoint}: {e}")
        return None
    except Exception as e:
        logging.error(f"Unexpected error for {endpoint}: {e}")
        return None

def post_api_data(endpoint, data):
    """Post data to the API"""
    try:
        headers = {"Authorization": f"Bearer {API_TOKEN}", "Content-Type": "application/json"}
        response = requests.post(f"{API_BASE_URL}{endpoint}", headers=headers, json=data, timeout=15)
        if response.status_code in [200, 201]:
            return response.json()
        else:
            logging.error(f"API POST request failed with status {response.status_code} for endpoint {endpoint}")
            return None
    except requests.exceptions.ConnectionError as e:
        logging.error(f"API connection failed: Cannot connect to {API_BASE_URL}{endpoint} - {e}")
        return None
    except requests.exceptions.Timeout as e:
        logging.error(f"API request timeout: {endpoint} - {e}")
        return None
    except requests.exceptions.RequestException as e:
        logging.error(f"API POST request failed for {endpoint}: {e}")
        return None
    except Exception as e:
        logging.error(f"Unexpected error for {endpoint}: {e}")
        return None

def delete_api_data(endpoint):
    """Delete data from the API"""
    try:
        headers = {"Authorization": f"Bearer {API_TOKEN}"}
        url = f"{API_BASE_URL}{endpoint}"
        logging.info(f"Sending DELETE request to: {url}")
        response = requests.delete(url, headers=headers, timeout=15)
        logging.info(f"DELETE response status: {response.status_code}")
        if response.status_code in [200, 204]:
            return response.json() if response.content else {"message": "Success"}
        else:
            logging.error(f"API DELETE request failed with status {response.status_code} for endpoint {endpoint}")
            logging.error(f"Response content: {response.text}")
            return None
    except requests.exceptions.ConnectionError as e:
        logging.error(f"API connection failed: Cannot connect to {API_BASE_URL}{endpoint} - {e}")
        return None
    except requests.exceptions.Timeout as e:
        logging.error(f"API request timeout: {endpoint} - {e}")
        return None
    except requests.exceptions.RequestException as e:
        logging.error(f"API DELETE request failed for {endpoint}: {e}")
        return None
    except Exception as e:
        logging.error(f"Unexpected error for {endpoint}: {e}")
        return None

def test_api_connection():
    """Test if API is accessible"""
    try:
        headers = {"Authorization": f"Bearer {API_TOKEN}"}
        response = requests.get(f"{API_BASE_URL}/health", headers=headers, timeout=5)
        return response.status_code == 200
    except:
        return False

def update_system_data():
    """Update system data in background thread"""
    global system_status, accounts_data, trades_data, logs_data, copy_configs_data
    
    # Wait a bit for the API server to start
    time.sleep(5)
    
    # Test connection before starting the loop
    if not test_api_connection():
        logging.warning("API server not accessible, will retry in background")
    
    while True:
        try:
            # Update system status
            status = fetch_api_data("/status")
            if status is not None:
                system_status = status
                socketio.emit('system_status_update', status)
            
            # Update accounts data
            accounts = fetch_api_data("/accounts")
            if accounts is not None:
                accounts_data = accounts
                socketio.emit('accounts_update', accounts)
            
            # Update copy trading configurations
            copy_configs = fetch_api_data("/copy-trading-config")
            if copy_configs is not None:
                copy_configs_data = copy_configs
                socketio.emit('copy_configs_update', copy_configs)
            
            # Update trades data
            trades = fetch_api_data("/trades")
            if trades is not None:
                trades_data = trades
                socketio.emit('trades_update', trades)
            
            # Update logs data with more detailed logging
            logs = fetch_api_data("/logs", {"limit": 100})  # Increased limit
            if logs is not None:
                logs_data = logs
                socketio.emit('logs_update', logs)
                logging.info(f"📋 Updated {len(logs)} logs to dashboard")
            else:
                logging.warning("⚠️ No logs data received from API")
                
        except Exception as e:
            logging.error(f"Error updating system data: {e}")
        
        time.sleep(15)  # Update every 15 seconds to reduce load

# Routes
@app.route('/')
def index():
    """Main dashboard page"""
    return render_template('dashboard.html')

@app.route('/accounts')
def accounts():
    """Accounts management page"""
    return render_template('accounts.html')

@app.route('/trades')
def trades():
    """Trades monitoring page"""
    return render_template('trades.html')

@app.route('/config')
def config():
    """Copy trading configuration page"""
    return render_template('config.html')

@app.route('/logs')
def logs():
    """System logs page"""
    return render_template('logs.html')

@app.route('/api/health')
def health_check():
    """Check API connectivity"""
    try:
        if test_api_connection():
            status = fetch_api_data("/health")
            return jsonify({"status": "connected", "api_status": status})
        else:
            return jsonify({"status": "disconnected", "error": "Cannot connect to API"})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)})

# API endpoints for dashboard
@app.route('/api/system/start', methods=['POST'])
def start_system():
    """Start copy trading system"""
    result = post_api_data("/start", {})
    if result:
        flash("Copy trading started successfully", "success")
    else:
        flash("Failed to start copy trading", "error")
    return redirect(url_for('index'))

@app.route('/api/system/stop', methods=['POST'])
def stop_system():
    """Stop copy trading system"""
    result = post_api_data("/stop", {})
    if result:
        flash("Copy trading stopped successfully", "success")
    else:
        flash("Failed to stop copy trading", "error")
    return redirect(url_for('index'))

@app.route('/api/system/initialize', methods=['POST'])
def initialize_system():
    """Initialize copy trading system"""
    result = post_api_data("/initialize", {})
    if result:
        flash("System initialized successfully", "success")
    else:
        flash("Failed to initialize system", "error")
    return redirect(url_for('index'))

@app.route('/api/system/force-check-trades', methods=['POST'])
def force_check_trades():
    """Force immediate check for new trades"""
    result = post_api_data("/force-check-trades", {})
    if result:
        flash("Manual trade check completed - check logs for details", "success")
    else:
        flash("Failed to check trades", "error")
    return redirect(url_for('index'))

@app.route('/api/accounts/create', methods=['POST'])
def create_account():
    """Create new account"""
    try:
        data = {
            "name": request.form['name'],
            "api_key": request.form['api_key'],
            "secret_key": request.form['secret_key'],
            "is_master": request.form.get('is_master') == 'on',
            "leverage": int(request.form.get('leverage', 10)),
            "risk_percentage": float(request.form.get('risk_percentage', 10.0))
        }
        
        result = post_api_data("/accounts", data)
        if result:
            flash("Account created successfully", "success")
        else:
            # Try to get more specific error information
            try:
                headers = {"Authorization": f"Bearer {API_TOKEN}", "Content-Type": "application/json"}
                response = requests.post(f"{API_BASE_URL}/accounts", headers=headers, json=data, timeout=15)
                if response.status_code == 400:
                    error_detail = response.json().get('detail', 'Invalid API credentials')
                    flash(f"Failed to create account: {error_detail}", "error")
                elif response.status_code == 500:
                    error_detail = response.json().get('detail', 'Server error')
                    flash(f"Failed to create account: {error_detail}", "error")
                else:
                    flash("Failed to create account. Please check your API credentials and try again.", "error")
            except:
                flash("Failed to create account. Please check your API credentials and try again.", "error")
    except Exception as e:
        logging.error(f"Error creating account: {e}")
        flash("Failed to create account. Please try again.", "error")
    
    return redirect(url_for('accounts'))

@app.route('/api/accounts/<int:account_id>/delete', methods=['POST'])
def delete_account(account_id):
    """Delete account"""
    logging.info(f"Dashboard delete_account called for account_id: {account_id}")
    result = delete_api_data(f"/accounts/{account_id}")
    if result:
        flash("Account deleted successfully", "success")
    else:
        flash("Failed to delete account", "error")
    return redirect(url_for('accounts'))

@app.route('/api/config/create', methods=['POST'])
def create_copy_config():
    """Create copy trading configuration"""
    data = {
        "master_account_id": int(request.form['master_account_id']),
        "follower_account_id": int(request.form['follower_account_id']),
        "copy_percentage": float(request.form.get('copy_percentage', 100.0)),
        "risk_multiplier": float(request.form.get('risk_multiplier', 1.0)),
        "max_risk_percentage": float(request.form.get('max_risk_percentage', 50.0))
    }
    
    result = post_api_data("/copy-trading-config", data)
    if result:
        flash("Copy trading configuration created successfully", "success")
        # Emit update to refresh configs in real-time
        fresh_configs = fetch_api_data("/copy-trading-config")
        if fresh_configs is not None:
            global copy_configs_data
            copy_configs_data = fresh_configs
            socketio.emit('copy_configs_update', fresh_configs)
    else:
        flash("Failed to create copy trading configuration", "error")
    return redirect(url_for('config'))

@app.route('/api/config/<int:config_id>/delete', methods=['POST'])
def delete_copy_config(config_id):
    """Delete copy trading configuration"""
    result = delete_api_data(f"/copy-trading-config/{config_id}")
    if result:
        flash("Copy trading configuration deleted successfully", "success")
        # Emit update to refresh configs in real-time
        fresh_configs = fetch_api_data("/copy-trading-config")
        if fresh_configs is not None:
            global copy_configs_data
            copy_configs_data = fresh_configs
            socketio.emit('copy_configs_update', fresh_configs)
    else:
        flash("Failed to delete copy trading configuration", "error")
    return redirect(url_for('config'))

@app.route('/api/logs/cleanup', methods=['POST'])
def cleanup_logs():
    """Clean up old system logs"""
    result = post_api_data("/logs/cleanup", {"max_logs_per_level": 500})
    if result:
        flash(f"Log cleanup completed: {result.get('cleaned_count', 0)} old logs removed", "success")
    else:
        flash("Failed to clean up logs", "error")
    return redirect(url_for('logs'))

@app.route('/api/logs/clear-all', methods=['POST'])
def clear_all_logs():
    """Clear ALL system logs"""
    result = delete_api_data("/logs/clear-all")
    if result:
        flash(f"All logs cleared successfully: {result.get('cleared_count', 0)} logs removed", "success")
    else:
        flash("Failed to clear all logs", "error")
    return redirect(url_for('logs'))

# WebSocket events
@socketio.on('connect')
def handle_connect():
    """Handle client connection"""
    emit('connected', {'data': 'Connected to copy trading dashboard'})

@socketio.on('disconnect')
def handle_disconnect():
    """Handle client disconnection"""
    print('Client disconnected')

@socketio.on('request_system_status')
def handle_system_status_request():
    """Handle system status request"""
    global system_status
    try:
        # Try to fetch fresh data from API
        fresh_status = fetch_api_data("/status")
        if fresh_status is not None:
            system_status = fresh_status
        emit('system_status_update', system_status if system_status else {})
    except Exception as e:
        logging.error(f"Error handling system status request: {e}")
        emit('system_status_update', {})

@socketio.on('request_accounts')
def handle_accounts_request():
    """Handle accounts request"""
    global accounts_data
    try:
        # Try to fetch fresh data from API
        fresh_accounts = fetch_api_data("/accounts")
        if fresh_accounts is not None:
            accounts_data = fresh_accounts
        emit('accounts_update', accounts_data if accounts_data else [])
    except Exception as e:
        logging.error(f"Error handling accounts request: {e}")
        emit('accounts_update', [])

@socketio.on('request_trades')
def handle_trades_request():
    """Handle trades request"""
    emit('trades_update', trades_data)

@socketio.on('request_logs')
def handle_logs_request():
    """Handle logs request with fresh data fetch"""
    global logs_data
    try:
        # Try to fetch fresh logs from API
        fresh_logs = fetch_api_data("/logs", {"limit": 100})
        if fresh_logs is not None:
            logs_data = fresh_logs
            logging.info(f"📋 Fetched {len(fresh_logs)} fresh logs for dashboard")
        else:
            logging.warning("⚠️ Could not fetch fresh logs, using cached data")
        
        emit('logs_update', logs_data if logs_data else [])
    except Exception as e:
        logging.error(f"Error handling logs request: {e}")
        emit('logs_update', [])

@socketio.on('request_copy_configs')
def handle_copy_configs_request():
    """Handle copy trading configs request"""
    global copy_configs_data
    try:
        # Try to fetch fresh data from API
        fresh_configs = fetch_api_data("/copy-trading-config")
        if fresh_configs is not None:
            copy_configs_data = fresh_configs
        emit('copy_configs_update', copy_configs_data if copy_configs_data else [])
    except Exception as e:
        logging.error(f"Error handling copy configs request: {e}")
        emit('copy_configs_update', [])

if __name__ == '__main__':
    # Start background thread for data updates
    update_thread = threading.Thread(target=update_system_data, daemon=True)
    update_thread.start()
    
    # Run the Flask app
    socketio.run(app, host='0.0.0.0', port=5000, debug=False, use_reloader=False, allow_unsafe_werkzeug=True)
