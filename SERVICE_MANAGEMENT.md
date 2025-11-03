# AMC Alert Pipeline - Service Management Guide

This guide shows you how to manage the AMC Alert Pipeline service on your Raspberry Pi.

## Installation

### 1. Transfer Files to Raspberry Pi

Copy the entire project directory to your Raspberry Pi:

```bash
# From your local machine:
scp -r /path/to/amc_showtime_alert pi@raspberrypi.local:~/
```

Or use rsync for faster transfers:

```bash
rsync -avz --exclude 'output/' --exclude 'logs/' --exclude '*.pyc' \
  /path/to/amc_showtime_alert/ pi@raspberrypi.local:~/amc_showtime_alert/
```

### 2. Install Dependencies on Raspberry Pi

SSH into your Raspberry Pi and install the package:

```bash
ssh pi@raspberrypi.local

# Navigate to the project directory
cd ~/amc_showtime_alert

# Install the package and dependencies
pip3 install -e .

# Or install dependencies manually:
pip3 install requests beautifulsoup4 schedule
```

### 3. Set Up Environment Variables

Create a `.env` file with your Telegram credentials:

```bash
cd ~/amc_showtime_alert
nano .env
```

Add your credentials:

```bash
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_CHAT_IDS=your_chat_id_here
```

Save and exit (Ctrl+X, Y, Enter).

### 4. Test the Script

Before installing the service, verify the script works:

```bash
cd ~/amc_showtime_alert
python3 run_alert_pipeline.py --server
```

Press Ctrl+C to stop. If it works, proceed to install the service.

### 5. Install the systemd Service

Run the installation script:

```bash
cd ~/amc_showtime_alert
./install-service.sh
```

The script will:
- Copy the service file to `/etc/systemd/system/`
- Update paths to match your installation directory
- Reload the systemd daemon
- Show you the next steps

## Basic Service Commands

### Start the Service

```bash
sudo systemctl start alert-pipeline.service
```

### Stop the Service

```bash
sudo systemctl stop alert-pipeline.service
```

### Restart the Service

```bash
sudo systemctl restart alert-pipeline.service
```

### Check Service Status

```bash
sudo systemctl status alert-pipeline.service
```

This shows:
- Whether the service is running
- Recent log messages
- Process ID (PID)
- Memory usage
- How long it's been running

### Enable Auto-Start on Boot

```bash
sudo systemctl enable alert-pipeline.service
```

This ensures the service starts automatically when your Raspberry Pi boots.

### Disable Auto-Start on Boot

```bash
sudo systemctl disable alert-pipeline.service
```

## Viewing Logs

### View Live Logs (Follow Mode)

```bash
sudo journalctl -u alert-pipeline.service -f
```

Press Ctrl+C to stop following.

### View Last 50 Lines

```bash
sudo journalctl -u alert-pipeline.service -n 50
```

### View All Logs

```bash
sudo journalctl -u alert-pipeline.service
```

### View Logs from Today

```bash
sudo journalctl -u alert-pipeline.service --since today
```

### View Logs from Last Hour

```bash
sudo journalctl -u alert-pipeline.service --since "1 hour ago"
```

### View Logs Between Times

```bash
sudo journalctl -u alert-pipeline.service --since "2025-11-02 10:00:00" --until "2025-11-02 12:00:00"
```

### View Only Error Messages

```bash
sudo journalctl -u alert-pipeline.service -p err
```

### Export Logs to File

```bash
sudo journalctl -u alert-pipeline.service > alert-pipeline-logs.txt
```

## Remote Management via SSH

You can manage the service from your laptop without staying connected:

### Check Status Remotely

```bash
ssh pi@raspberrypi.local "sudo systemctl status alert-pipeline.service"
```

### View Logs Remotely

```bash
ssh pi@raspberrypi.local "sudo journalctl -u alert-pipeline.service -n 50"
```

### Restart Service Remotely

```bash
ssh pi@raspberrypi.local "sudo systemctl restart alert-pipeline.service"
```

### Check if Service is Running

```bash
ssh pi@raspberrypi.local "sudo systemctl is-active alert-pipeline.service"
```

Returns `active` if running, `inactive` if stopped.

## Monitoring Status Logs

The service writes simple status logs to `logs/status_YYYY-WW.log` (weekly rotation).

### View Status Logs

```bash
# On Raspberry Pi:
tail -f ~/amc_showtime_alert/logs/status_*.log

# Remotely:
ssh pi@raspberrypi.local "tail -f ~/amc_showtime_alert/logs/status_*.log"
```

### Check Latest Status

```bash
ssh pi@raspberrypi.local "tail -5 ~/amc_showtime_alert/logs/status_*.log"
```

## Troubleshooting

### Error: "Failed to determine user credentials" or "Failed at step USER"

This error means the `User=` setting in the service file doesn't match a valid user on your system.

**Cause:** The service file has `User=pi` but your username is different (e.g., `jim`, `ubuntu`, etc.)

**Solution 1 - Reinstall with the install script (Recommended):**

The install script automatically detects your username. Just run it again:

```bash
cd ~/amc_showtime_alert
./install-service.sh
```

The script will show you the detected username and update the service file accordingly.

**Solution 2 - Manual fix:**

1. Check your current username:
   ```bash
   whoami
   ```

2. Edit the service file:
   ```bash
   sudo nano /etc/systemd/system/alert-pipeline.service
   ```

3. Change the `User=` line to match your username:
   ```ini
   User=jim  # Replace with your actual username
   ```

4. Save and reload:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl restart alert-pipeline.service
   ```

### Service Won't Start

1. Check the service status for error messages:
   ```bash
   sudo systemctl status alert-pipeline.service
   ```

2. Check the full journal logs:
   ```bash
   sudo journalctl -u alert-pipeline.service -n 100
   ```

3. Verify Python can run the script:
   ```bash
   cd ~/amc_showtime_alert
   python3 run_alert_pipeline.py --server
   ```

4. Check file permissions:
   ```bash
   ls -la ~/amc_showtime_alert/run_alert_pipeline.py
   ```

### Service Crashes Immediately

The service is configured to restart automatically after 3 seconds. Check logs:

```bash
sudo journalctl -u alert-pipeline.service -n 100 -p err
```

Common issues:
- Missing `.env` file with Telegram credentials
- Missing Python dependencies (run `pip3 install -e .`)
- Wrong paths in service file
- Config.json syntax errors

### View Service Configuration

```bash
sudo systemctl cat alert-pipeline.service
```

### Reload Service After Editing Config

If you modify `config.json`:

```bash
# No need to restart the service - it reads config on each run
# But if you want to apply changes immediately:
sudo systemctl restart alert-pipeline.service
```

### Check Resource Usage

```bash
# CPU and memory usage
ps aux | grep run_alert_pipeline

# More detailed system resource view
htop
# (press F4 to filter, type "python", Enter)
```

### Service Logs Show "Permission Denied"

Check the `User` in the service file matches your username:

```bash
sudo systemctl cat alert-pipeline.service | grep User
```

If needed, edit the service file:

```bash
sudo nano /etc/systemd/system/alert-pipeline.service
# Change User=pi to your actual username
sudo systemctl daemon-reload
sudo systemctl restart alert-pipeline.service
```

## Updating the Code

When you make changes to the code:

### 1. Transfer Updated Files

```bash
# From your local machine:
rsync -avz --exclude 'output/' --exclude 'logs/' \
  /path/to/amc_showtime_alert/ pi@raspberrypi.local:~/amc_showtime_alert/
```

### 2. Restart the Service

```bash
ssh pi@raspberrypi.local "sudo systemctl restart alert-pipeline.service"
```

### 3. Verify It's Running

```bash
ssh pi@raspberrypi.local "sudo systemctl status alert-pipeline.service"
```

## Uninstalling the Service

To remove the service:

```bash
# Stop the service
sudo systemctl stop alert-pipeline.service

# Disable auto-start
sudo systemctl disable alert-pipeline.service

# Remove service file
sudo rm /etc/systemd/system/alert-pipeline.service

# Reload systemd
sudo systemctl daemon-reload
```

## Advanced: Changing Service Configuration

To modify service settings (e.g., restart behavior, user, environment):

```bash
# Edit the service file
sudo nano /etc/systemd/system/alert-pipeline.service

# Reload systemd to apply changes
sudo systemctl daemon-reload

# Restart the service
sudo systemctl restart alert-pipeline.service
```

Common modifications:

**Change restart delay:**
```ini
RestartSec=10  # Wait 10 seconds before restart (default is 3)
```

**Add environment variables:**
```ini
Environment="TELEGRAM_BOT_TOKEN=your_token"
Environment="TELEGRAM_CHAT_IDS=your_chat_id"
```

**Change user:**
```ini
User=your_username  # Replace with your actual username
```

**Limit restart attempts:**
```ini
StartLimitBurst=5  # Max 5 restart attempts
StartLimitIntervalSec=10m  # Within 10 minutes
```

## Quick Reference Card

| Task | Command |
|------|---------|
| Start service | `sudo systemctl start alert-pipeline.service` |
| Stop service | `sudo systemctl stop alert-pipeline.service` |
| Restart service | `sudo systemctl restart alert-pipeline.service` |
| Check status | `sudo systemctl status alert-pipeline.service` |
| Enable on boot | `sudo systemctl enable alert-pipeline.service` |
| Disable on boot | `sudo systemctl disable alert-pipeline.service` |
| View live logs | `sudo journalctl -u alert-pipeline.service -f` |
| View last 50 logs | `sudo journalctl -u alert-pipeline.service -n 50` |
| Is running? | `sudo systemctl is-active alert-pipeline.service` |
| View config | `sudo systemctl cat alert-pipeline.service` |
| Reload systemd | `sudo systemctl daemon-reload` |

## Configuration

The service respects all settings in `config.json`:

- **Interval**: `server.interval_minutes` (default: 60 minutes)
- **Cleanup**: `server.cleanup_interval_days` (default: 7 days)
- **Status Logs**: `logging.enable_status_file_logging` (default: true)
- **Retention**: `telegram.retention_days` (default: 30 days)

Edit `config.json` and restart the service to apply changes.

## Getting Help

If you encounter issues:

1. Check service status: `sudo systemctl status alert-pipeline.service`
2. Check recent logs: `sudo journalctl -u alert-pipeline.service -n 100`
3. Check error logs: `sudo journalctl -u alert-pipeline.service -p err`
4. Verify script works manually: `python3 ~/amc_showtime_alert/run_alert_pipeline.py --server`
5. Check Python dependencies: `pip3 list | grep -E "requests|beautifulsoup4|schedule"`
