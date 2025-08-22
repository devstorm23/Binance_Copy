# Copy Trading Bot

A comprehensive copy trading system for Binance futures trading that enables automatic mirroring of trades from master accounts to multiple follower accounts with advanced risk management and real-time monitoring.

## Features

### 🚀 Core Features
- **Multi-account Support**: Securely connect and manage multiple Binance accounts
- **Real-time Copy Trading**: Instant synchronization of all trades from master to follower accounts
- **Proportional Sizing**: Intelligent position sizing based on each account's balance and risk settings
- **Leverage Management**: Configurable leverage settings for each individual account
- **Privacy & Independence**: Works directly with Binance API - no third-party dependencies
- **Real-time Monitoring**: Live dashboard for oversight of all accounts and trades
- **Security**: Fully encrypted API keys and data with best practices

### 📊 Advanced Features
- **Risk Management**: Configurable risk percentages and position sizing
- **Order Types**: Support for Market, Limit, Stop Market, and Take Profit orders
- **Trade Filtering**: Advanced filtering and search capabilities
- **System Logging**: Comprehensive logging and monitoring
- **REST API**: Full programmatic access to all features
- **Web Dashboard**: Modern, responsive web interface

## Quick Start

### Prerequisites
- Python 3.8 or higher
- Binance account with API access
- At least one master account and one follower account

### Installation

1. **Clone the repository**
   ```bash
   git clone <repository-url>
   cd copy-trading-bot
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure environment**
   ```bash
   cp env_example.txt .env
   # Edit .env with your configuration
   ```

4. **Run the application**
   ```bash
   python main.py
   ```

5. **Access the dashboard**
   - Web Dashboard: http://localhost:5000
   - API Documentation: http://localhost:8000/docs

## Configuration

### Environment Variables

Create a `.env` file with the following variables:

```env
# Database
DATABASE_URL=sqlite:///./copy_trading.db

# Security
SECRET_KEY=your-super-secret-key
API_TOKEN=your-secret-token

# Binance API (for testing)
BINANCE_TESTNET=true

# Copy Trading Settings
DEFAULT_LEVERAGE=10
DEFAULT_RISK_PERCENTAGE=10.0
MAX_LEVERAGE=20

# Logging
LOG_LEVEL=INFO
```

### Binance API Setup

1. **Create API Keys**
   - Log into your Binance account
   - Go to API Management
   - Create new API key with futures trading permissions
   - Enable futures trading and disable spot trading for security

2. **Required Permissions**
   - Futures Trading
   - Read Info
   - Enable Futures

3. **Security Settings**
   - Set IP restrictions if possible
   - Enable 2FA on your Binance account
   - Never share your API keys

## Usage

### Setting Up Accounts

1. **Add Master Account**
   - Go to Accounts page in dashboard
   - Click "Add New Account"
   - Enter account name and API credentials
   - Check "Master Account" checkbox
   - Set leverage and risk percentage

2. **Add Follower Accounts**
   - Repeat the process for follower accounts
   - Do NOT check "Master Account" checkbox
   - Configure individual risk settings

3. **Create Copy Trading Configuration**
   - Go to Configuration page
   - Select master and follower accounts
   - Set copy percentage and risk multiplier
   - Save configuration

### Starting Copy Trading

1. **Initialize System**
   - Click "Initialize System" on dashboard
   - Verify all accounts are connected

2. **Start Copy Trading**
   - Click "Start Copy Trading"
   - Monitor the dashboard for activity

3. **Monitor Performance**
   - Use the dashboard to monitor trades
   - Check logs for any issues
   - Review account balances and positions

## API Reference

### Authentication
All API endpoints require Bearer token authentication:
```
Authorization: Bearer your-secret-token
```

### Key Endpoints

#### Accounts
- `POST /accounts` - Create new account
- `GET /accounts` - List all accounts
- `GET /accounts/{id}` - Get account details
- `PUT /accounts/{id}` - Update account
- `DELETE /accounts/{id}` - Delete account

#### Copy Trading Configuration
- `POST /copy-trading-config` - Create configuration
- `GET /copy-trading-config` - List configurations
- `PUT /copy-trading-config/{id}` - Update configuration

#### Trades
- `GET /trades` - List all trades
- `POST /trades` - Create manual trade

#### System Control
- `POST /initialize` - Initialize system
- `POST /start` - Start copy trading
- `POST /stop` - Stop copy trading
- `GET /status` - Get system status

### Example API Usage

```python
import requests

# Initialize system
response = requests.post(
    "http://localhost:8000/initialize",
    headers={"Authorization": "Bearer your-secret-token"}
)

# Add account
account_data = {
    "name": "My Master Account",
    "api_key": "your-api-key",
    "secret_key": "your-secret-key",
    "is_master": True,
    "leverage": 10,
    "risk_percentage": 10.0
}

response = requests.post(
    "http://localhost:8000/accounts",
    json=account_data,
    headers={"Authorization": "Bearer your-secret-token"}
)
```

## Risk Management

### Position Sizing
The bot calculates position sizes using:
```
Position Size = (Account Balance × Risk Percentage × Risk Multiplier) / (Leverage × Current Price)
```

### Risk Controls
- **Maximum Leverage**: Configurable per account
- **Risk Percentage**: Percentage of account balance per trade
- **Copy Percentage**: Percentage of master trades to copy
- **Risk Multiplier**: Additional risk adjustment factor

### Safety Features
- Minimum and maximum order size limits
- Automatic leverage setting
- Real-time balance monitoring
- Comprehensive error handling and logging

## Monitoring and Logging

### Dashboard Features
- Real-time system status
- Account overview and balances
- Trade history and statistics
- System logs and error monitoring
- Performance metrics

### Log Levels
- **ERROR**: Critical errors requiring attention
- **WARNING**: Potential issues to monitor
- **INFO**: General system information
- **DEBUG**: Detailed debugging information

### Monitoring Best Practices
1. **Regular Checks**: Monitor dashboard daily
2. **Log Review**: Check logs for errors or warnings
3. **Balance Monitoring**: Track account balances
4. **Performance Review**: Analyze trade performance
5. **Backup**: Regular database backups

## Security Considerations

### API Key Security
- Store API keys securely in environment variables
- Use different API keys for master and follower accounts
- Enable IP restrictions on Binance API keys
- Regularly rotate API keys

### System Security
- Use strong passwords and secrets
- Keep the system updated
- Monitor for unauthorized access
- Regular security audits

### Trading Security
- Start with small amounts for testing
- Use testnet for initial setup
- Monitor all trades closely
- Set appropriate risk limits

## Troubleshooting

### Common Issues

1. **Connection Errors**
   - Verify API keys are correct
   - Check internet connection
   - Ensure Binance API is accessible

2. **Trade Failures**
   - Check account balance
   - Verify leverage settings
   - Review symbol restrictions

3. **System Errors**
   - Check logs for error details
   - Verify database connection
   - Restart the application

### Debug Mode
Enable debug logging by setting:
```env
LOG_LEVEL=DEBUG
```

### Support
For issues and questions:
1. Check the logs for error details
2. Review the documentation
3. Test with small amounts first
4. Contact support with detailed error information

## Development

### Project Structure
```
copy-trading-bot/
├── main.py                 # Main application entry point
├── config.py              # Configuration management
├── models.py              # Database models
├── binance_client.py      # Binance API client
├── copy_trading_engine.py # Core copy trading logic
├── api.py                 # FastAPI REST API
├── dashboard.py           # Flask web dashboard
├── templates/             # HTML templates
├── requirements.txt       # Python dependencies
└── README.md             # This file
```

### Contributing
1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests if applicable
5. Submit a pull request

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Disclaimer

This software is for educational and informational purposes only. Trading cryptocurrencies involves substantial risk of loss and is not suitable for all investors. The value of cryptocurrencies can go down as well as up, and you may lose some or all of your investment.

- Past performance does not guarantee future results
- Always test thoroughly before using with real funds
- Start with small amounts
- Monitor the system continuously
- Never invest more than you can afford to lose

## Version History

### v1.0.0
- Initial release
- Multi-account copy trading
- Web dashboard
- REST API
- Risk management
- Real-time monitoring
