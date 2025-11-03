#!/bin/bash
#
# Installation script for AMC Alert Pipeline systemd service
# Run this script on your Raspberry Pi to install the service
#

set -e  # Exit on any error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}AMC Alert Pipeline Service Installer${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""

# Check if running as root
if [ "$EUID" -eq 0 ]; then
    echo -e "${RED}Error: Do not run this script as root (don't use sudo)${NC}"
    echo -e "The script will ask for sudo password when needed."
    exit 1
fi

# Get the directory where this script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
SERVICE_FILE="${SCRIPT_DIR}/alert-pipeline.service"

echo -e "${YELLOW}Current directory:${NC} ${SCRIPT_DIR}"
echo ""

# Check if service file exists
if [ ! -f "$SERVICE_FILE" ]; then
    echo -e "${RED}Error: Service file not found at ${SERVICE_FILE}${NC}"
    exit 1
fi

# Verify Python script exists
PYTHON_SCRIPT="${SCRIPT_DIR}/run_alert_pipeline.py"
if [ ! -f "$PYTHON_SCRIPT" ]; then
    echo -e "${RED}Error: Python script not found at ${PYTHON_SCRIPT}${NC}"
    exit 1
fi

echo -e "${GREEN}✓${NC} Service file found: ${SERVICE_FILE}"
echo -e "${GREEN}✓${NC} Python script found: ${PYTHON_SCRIPT}"
echo ""

# Update the service file paths to match current directory
echo -e "${YELLOW}Updating service file paths...${NC}"
TEMP_SERVICE="/tmp/alert-pipeline.service"
sed "s|WorkingDirectory=/home/pi/amc_showtime_alert|WorkingDirectory=${SCRIPT_DIR}|g" "$SERVICE_FILE" | \
sed "s|ExecStart=/usr/bin/python3 -u /home/pi/amc_showtime_alert/run_alert_pipeline.py|ExecStart=/usr/bin/python3 -u ${SCRIPT_DIR}/run_alert_pipeline.py|g" > "$TEMP_SERVICE"

# Copy service file to systemd directory
echo -e "${YELLOW}Installing service file...${NC}"
sudo cp "$TEMP_SERVICE" /etc/systemd/system/alert-pipeline.service
sudo chmod 644 /etc/systemd/system/alert-pipeline.service

# Clean up temp file
rm "$TEMP_SERVICE"

echo -e "${GREEN}✓${NC} Service file installed to /etc/systemd/system/alert-pipeline.service"
echo ""

# Reload systemd daemon
echo -e "${YELLOW}Reloading systemd daemon...${NC}"
sudo systemctl daemon-reload
echo -e "${GREEN}✓${NC} Systemd daemon reloaded"
echo ""

# Check if service is already running
if systemctl is-active --quiet alert-pipeline.service; then
    echo -e "${YELLOW}⚠ Service is already running${NC}"
    echo ""
    read -p "Do you want to restart it with the new configuration? (y/N): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        sudo systemctl restart alert-pipeline.service
        echo -e "${GREEN}✓${NC} Service restarted"
    fi
else
    echo -e "${YELLOW}Service is not currently running${NC}"
fi

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}Installation Complete!${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo -e "Next steps:"
echo ""
echo -e "1. ${YELLOW}Enable service to start on boot:${NC}"
echo -e "   sudo systemctl enable alert-pipeline.service"
echo ""
echo -e "2. ${YELLOW}Start the service now:${NC}"
echo -e "   sudo systemctl start alert-pipeline.service"
echo ""
echo -e "3. ${YELLOW}Check service status:${NC}"
echo -e "   sudo systemctl status alert-pipeline.service"
echo ""
echo -e "4. ${YELLOW}View logs:${NC}"
echo -e "   sudo journalctl -u alert-pipeline.service -f"
echo ""
echo -e "For more commands, see ${GREEN}SERVICE_MANAGEMENT.md${NC}"
echo ""
