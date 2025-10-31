#!/bin/bash
# Installation script for Meshtastic Weather Station service

echo "Installing Meshtastic Weather Station as a system service..."

# Copy service file to systemd directory
sudo cp ws4m.service /etc/systemd/system/

# Reload systemd daemon
sudo systemctl daemon-reload

# Enable the service to start on boot
sudo systemctl enable ws4m.service

echo ""
echo "Installation complete!"
echo ""
echo "Available commands:"
echo "  Start service:   sudo systemctl start ws4m"
echo "  Stop service:    sudo systemctl stop ws4m"
echo "  View status:     sudo systemctl status ws4m"
echo "  View logs:       sudo journalctl -u ws4m -f"
echo "  Disable autorun: sudo systemctl disable ws4m"
echo ""
echo "The service will now start automatically on boot."
echo "To start it now, run: sudo systemctl start ws4m"
