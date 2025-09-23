# 🔋 Power Monitor Bot

A cross-platform Telegram bot that monitors your device's power/charging status and sends real-time notifications when power states change.

## ✨ Features

- **Cross-Platform Support**: Works on Windows, Linux, macOS, and Android (Termux)
- **Real-Time Notifications**: Get instant alerts when power is connected or disconnected
- **Interactive Menu**: User-friendly inline keyboard interface
- **Multiple Operation Modes**: Admin-only or multi-user modes
- **Battery Status**: Check current battery percentage and charging status
- **Persistent Configuration**: Saves settings and user registrations
- **Environment Variable Support**: Perfect for containerized deployments
- **Fallback Detection**: Multiple battery detection methods for maximum compatibility

## 🚀 Quick Start

### Prerequisites

- Python 3.6+
- Telegram Bot Token (get one from [@BotFather](https://t.me/botfather))

### Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/Shadhink412/power-check.git
   cd power-check
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Run the bot**
   ```bash
   python app.py
   ```

4. **First-time setup**
   - The bot will prompt you for your Telegram Bot Token
   - Choose between admin-only or multi-user mode
   - Set polling interval (default: 5 seconds)

## 🔧 Configuration

### Interactive Setup
On first run, the bot will guide you through an interactive setup process.

### Environment Variables (Recommended for Docker/Containers)
```bash
BOT_TOKEN=your_bot_token_here
BOT_MODE=multi                    # or "admin" for admin-only mode
ADMIN_IDS=123456789,987654321    # comma-separated admin chat IDs (required for admin mode)
POLL_INTERVAL=5                   # polling interval in seconds (optional)
```

### Configuration File
Settings are automatically saved to `data.json` and include:
- Bot token
- Operation mode
- Admin and registered user IDs
- Platform detection
- Polling interval

## 🎮 Bot Commands

### User Commands
- **🔋 Battery Status** - Get current power and battery information
- **📝 Register** - Register for power change notifications
- **🚫 Unregister** - Stop receiving notifications
- **❓ Help** - Show help message

### Admin Commands
- **⚙️ Reconfigure** - Re-run configuration setup (admin only)

## 🖥️ Platform Support

| Platform | Detection Method | Fallback |
|----------|------------------|----------|
| **Windows** | psutil | - |
| **Linux** | psutil | sysfs (`/sys/class/power_supply/`) |
| **macOS** | psutil | - |
| **Android (Termux)** | termux-battery-status | sysfs |

## 🐳 Docker Deployment

Create a `docker-compose.yml`:

```yaml
version: '3.8'
services:
  power-monitor:
    build: .
    environment:
      - BOT_TOKEN=your_bot_token_here
      - BOT_MODE=multi
      - POLL_INTERVAL=5
    volumes:
      - ./data:/app/data
    restart: unless-stopped
```

## 🔄 Running as a Service

### Linux (systemd)
Create `/etc/systemd/system/power-monitor.service`:

```ini
[Unit]
Description=Power Monitor Bot
After=network.target

[Service]
Type=simple
User=your_user
WorkingDirectory=/path/to/power-chk
ExecStart=/usr/bin/python3 /path/to/power-chk/app.py
Restart=always
RestartSec=10
Environment=BOT_TOKEN=your_token_here

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl enable power-monitor
sudo systemctl start power-monitor
```

### Windows (Task Scheduler)
1. Open Task Scheduler
2. Create Basic Task
3. Set trigger to "At startup"
4. Set action to start `python.exe` with your script path
5. Configure to run whether user is logged on or not

## 📱 Usage Examples

1. **Start the bot**: Send `/start` to your bot
2. **Register for notifications**: Click "📝 Register" 
3. **Check battery status**: Click "🔋 Battery Status"
4. **Get help**: Click "❓ Help"

The bot will automatically send notifications when:
- ⚡ Power is disconnected
- 🔌 Power is connected

## 🛠️ Development

### Project Structure
```
power-chk/
├── app.py              # Main bot application
├── requirements.txt    # Python dependencies
├── data.json          # Configuration file (auto-generated)
└── README.md          # This file
```

### Key Components
- **Platform Detection**: Automatically detects Windows, Linux, macOS, or Android
- **Battery Reading**: Multiple fallback methods for reliable battery status
- **Telegram Integration**: Full bot API with inline keyboards
- **State Management**: Persistent storage of configuration and user data
- **Error Handling**: Graceful handling of network issues and platform limitations

## 🔒 Security Notes

- Keep your `data.json` file secure as it contains your bot token
- In admin-only mode, only specified admin IDs can interact with the bot
- The bot doesn't store any personal information beyond Telegram chat IDs

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🐛 Troubleshooting

### Common Issues

**Bot doesn't respond**
- Check if the bot token is correct
- Ensure the bot is running and connected to the internet
- Verify you've started a chat with the bot

**Battery status not available**
- On Linux: Check if `/sys/class/power_supply/` exists
- On Android: Install Termux:API addon
- On Windows: Ensure psutil is installed correctly

**Permission errors**
- Ensure the script has write permissions for `data.json`
- On Linux: Check systemd service user permissions

### Getting Help

If you encounter issues:
1. Check the console output for error messages
2. Verify your platform is supported
3. Ensure all dependencies are installed
4. Create an issue on GitHub with error details

## ⭐ Show Your Support

If this project helped you, please consider giving it a star on GitHub!

---

**Made with ❤️ for cross-platform power monitoring**
