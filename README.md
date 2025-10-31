# Meshtastic Weather Station for Raspberry Pi 5

Python project to read temperature and humidity data from a DHT22 sensor connected to a Raspberry Pi 5 and send updates to Meshtastic nodes on the mesh network.

## Features

- Interactive menu system with auto-start option
- Multi-node support - configure multiple target nodes
- Smart routing - automatically sends to all other nodes when sender is in config
- Real-time temperature (°F) and humidity (%) from DHT22 sensor
- Signal strength (SNR) and hop count display
- CSV logging with 7-day retention and node statistics
- Reports menu to view nodes seen on the network
- Auto-reconnect for USB Meshtastic device
- Sensor reset functionality for improved reliability
- Message confirmation display
- Autostart on boot capability

## Hardware Requirements

- Raspberry Pi 5 (or Pi Zero 2 W - same pinout)
- DHT22 (AM2302) Temperature & Humidity Sensor
- 3 female-to-female jumper wires
- (Optional) 10kΩ pull-up resistor if not built into sensor module
- Meshtastic device connected via USB

## Wiring

Connect the DHT22 sensor to your Raspberry Pi 5 as follows:

| DHT22 Pin | Wire Color | Pi 5 Pin | Description |
|-----------|------------|----------|-------------|
| Pin 1 (VCC) | Red | Pin 1 | 3.3V Power |
| Pin 2 (DATA) | Yellow | Pin 7 | GPIO4 |
| Pin 3 (NC) | - | - | Not Connected |
| Pin 4 (GND) | Black | Pin 6 | Ground |

See `WIRING.txt` for a detailed diagram and alternative GPIO pin options.

## Software Setup

### 1. Create Virtual Environment

```bash
cd /home/iain/WS
python3 -m venv venv
source venv/bin/activate
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

The required packages are:
- `adafruit-circuitpython-dht` - Library for DHT sensors
- `meshtastic` - Meshtastic Python API for messaging

### 3. Configure Nodes

Edit `config.ini` and configure your nodes:

```ini
[nodes]
yang = 2658499212
ying = 2658555560

[settings]
selected_node = ying
update_interval = 60
auto_boot_timeout = 10
usb_reconnect_interval = 10

[logging]
log_file = meshtastic_log.csv
auto_save_interval = 300
retention_days = 7
```

To find node IDs, use the Meshtastic CLI:
```bash
meshtastic --nodes
```

### 4. Run the Script

```bash
python ws4m.py
```

The script will display a menu with options to:
1. Start Sending Messages
2. Stop Sending Messages
3. Options (change settings)
4. Reports (view nodes seen)
5. View Sample Message
6. Exit

If no selection is made within 15 seconds, option 1 auto-starts.

## Message Format

Messages are formatted in 3 lines optimized for Meshtastic displays:

```
10/31 14:20 (6/114)
T: 83F 6.0_snr/0_hop
H: 25% 14:20:31
```

- **Line 1**: Date, time, (online nodes/total nodes)
- **Line 2**: Temperature in Fahrenheit, SNR (signal strength), hop count
- **Line 3**: Humidity percentage, military time

## Autostart Setup

### GUI Autostart (Recommended for Desktop Use)

The autostart file is already configured at:
```
/home/iain/.config/autostart/ws4m.desktop
```

To enable/disable:
```bash
# Already enabled - remove to disable
rm /home/iain/.config/autostart/ws4m.desktop

# Re-enable by copying back
cp /home/iain/WS/ws4m.desktop /home/iain/.config/autostart/
```

### Auto-Login Setup

To automatically log in to the GUI on boot:

1. **Using raspi-config (Recommended)**:
```bash
sudo raspi-config
```
- Select: `1 System Options`
- Select: `S5 Boot / Auto Login`
- Select: `B4 Desktop Autologin` (Desktop GUI, automatically logged in as 'iain')
- Select `<Finish>` and reboot

2. **Manual Configuration**:
Edit the LightDM configuration:
```bash
sudo nano /etc/lightdm/lightdm.conf
```

Find the `[Seat:*]` section and add/modify:
```ini
[Seat:*]
autologin-user=iain
autologin-user-timeout=0
```

Save and reboot:
```bash
sudo reboot
```

### Complete Hands-Free Setup

When both auto-login and GUI autostart are enabled:
1. Pi boots up
2. Auto-login to desktop as user `iain`
3. Terminal window opens with weather station
4. After 15 seconds, starts sending messages automatically

### Background Service (Alternative)

For headless operation or to run without a terminal window:

```bash
cd /home/iain/WS
./install_service.sh
```

Service commands:
```bash
sudo systemctl start ws4m      # Start now
sudo systemctl stop ws4m       # Stop service
sudo systemctl status ws4m     # Check status
sudo journalctl -u ws4m -f     # View live logs
sudo systemctl disable ws4m    # Disable autostart
```

## Menu Options

1. **Start Sending Messages** - Begin reading sensor and sending to mesh
2. **Stop Sending Messages** - Return to main menu
3. **Options** - Configure settings (target node, intervals, retention)
4. **Reports** - View nodes seen on the network
5. **View Sample Message** - Preview message format
6. **Exit** - Quit the program

## Customization

### Change Update Interval

From the Options menu (option 3), or edit `config.ini`:

```ini
update_interval = 120  # Send every 2 minutes instead
```

### Change GPIO Pin

Edit `ws4m.py` and modify the `DHT_PIN` variable:

```python
# Use GPIO18 instead of GPIO4
DHT_PIN = board.D18
```

Available GPIO pins: D4, D18, D22, D23, D24, etc.

### Add More Nodes

Edit `config.ini` under the `[nodes]` section:

```ini
[nodes]
yang = 2658499212
ying = 2658555560
newnode = 1234567890
```

## Troubleshooting

**Import errors**: Make sure the virtual environment is activated and dependencies are installed.

**GPIO errors ("Unable to set line 4 to input")**: 
- Script includes auto-cleanup at startup
- If persistent, reboot the Pi: `sudo reboot`

**DHT22 reading errors**: 
- Normal occasional errors are handled automatically
- Script resets sensor on errors
- Ensure proper wiring and 3.3V power

**Meshtastic connection failed**: 
- Ensure Meshtastic device is connected via USB
- Check device permissions: `sudo usermod -a -G dialout $USER` (logout/login required)
- Verify with: `meshtastic --info`
- Script auto-reconnects every 10 seconds

**No signal info (shows --/--)**: 
- Target node may not be in range or not heard from yet
- Signal info appears once target node is heard on the mesh

**Permission errors**: Add user to required groups:
```bash
sudo usermod -a -G dialout,gpio $USER
# Logout and login for changes to take effect
```

**Autostart terminal closes immediately**:
- Check the autostart file has correct venv path
- Test manually: `lxterminal -e bash -c "cd /home/iain/WS && source venv/bin/activate && python ws4m.py"`

## Compatibility

This project is designed for Raspberry Pi 5 but works on:
- Raspberry Pi 5
- Raspberry Pi Zero 2 W (same GPIO pinout)
- Raspberry Pi 4
- Raspberry Pi 3/3+
- Any Raspberry Pi with 40-pin GPIO header

## License

MIT License - Free to use and modify.
