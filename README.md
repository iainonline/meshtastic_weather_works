# Meshtastic Weather Station for Raspberry Pi 5

**Version 2.0** - Advanced mesh messaging with ACK confirmation, PKI encryption, and SNR analytics

Python project to read temperature and humidity data from a DHT22 sensor connected to a Raspberry Pi 5 and send updates to Meshtastic nodes on the mesh network with delivery confirmation and signal strength tracking.

## Version 2.0 Features

### Core Features
- **Interactive menu system** with auto-start option
- **Multi-node support** - configure multiple target nodes
- **Smart routing** - automatically sends to all other nodes when sender is in config
- **Real-time temperature** (°F) and humidity (%) from DHT22 sensor
- **Signal strength** (SNR) and hop count display in messages
- **CSV logging** with configurable retention and node statistics
- **Reports menu** to view nodes seen on the network
- **Auto-reconnect** for USB Meshtastic device
- **Sensor reset** functionality for improved reliability
- **Customizable message templates** via config.ini
- **Autostart on boot** capability

### New in Version 2.0

#### Advanced ACK Confirmation System
- **Delivery confirmation** - Uses ACK/NAK system to verify message delivery
- **Verbose ACK tracking** - Real-time console output shows ACK callback activity
- **ACK status indicators** - Messages show 'A' (acknowledged) or 'U' (unacknowledged)
- **Configurable ACK wait time** - Set delay before sending confirmation (default 30s for mesh)
- **Automatic retry** - Retries pending messages after configurable timeout (default 60s)
- **ACK confirmation messages** - Sender receives confirmation with timestamp and SNR
- **Real-time ACK notifications** - See confirmation on screen when ACK arrives
- **Compact status display** - Shows send time, ACK time, and SNR on sender

#### PKI Public Key Encryption
- **End-to-end encryption** - Use recipient's public key instead of shared channel keys
- **Auto-scan feature** - Menu option to scan and update public keys from nodes
- **Manual key entry** - Support for nodes without PKI enabled
- **Per-message encryption** - Choose PKI or channel encryption per node
- **Secure messaging** - Only recipient can decrypt with their private key

#### Channel Control
- **Configurable channel** - Prevent messages from appearing on public LongFast channel
- **Channel index setting** - Send to specific channel (0 = primary/private)
- **Privacy control** - Keep weather messages on private channels only

#### SNR Statistics & Analytics
- **All-time SNR tracking** - Track min (cutoff), max (optimal), and average SNR per node
- **Trend analysis** - View recent SNR values (last 10) to spot signal degradation
- **Time period tracking** - Shows first seen, last seen, and total duration
- **Persistent storage** - Stats saved to `snr_stats.json` and survive restarts
- **Reset capability** - Clear statistics with double confirmation safety
- **Diagnostic reports** - Identify weak signals and optimal conditions per node

#### Enhanced User Experience
- **Verbose ACK debugging** - Detailed console output for troubleshooting ACK issues
- **Clean countdown display** - Removed confusing retry messages from console
- **Status indicators in templates** - {ack} placeholder shows A/U status
- **Improved error messages** - Better feedback for ACK, PKI, and channel issues

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
message_template = template1
ack_retry_timeout = 60
ack_wait_time = 30
want_ack = on
mesh_send_mode = mesh
pki_encrypted = on
channel_index = 0

[public_keys]
yang = bOatKxov+G+kjVIzYP1bLV0sF1kktpVrhAMGwsMttVA=
ying = 0d5PCyDP0yUCEzH0AIcx1UUGoifdnuiMHRLEURNJTxM=

[logging]
log_file = meshtastic_log.csv
auto_save_interval = 300
retention_days = 7

[message_templates]
template1 = {date} {time} ({online}/{total})\nT: {temp}F {snr} SNR {hops} HOP {ack}\nH: {humidity}%% {time_detail} ({online}/{total})
template2 = {date} {time}\nNodes: {online}/{total}\nT: {temp}F SNR:{snr}\nH: {humidity}%% Hops:{hops} {ack}
template3 = {date} {time} T:{temp}F\nH: {humidity}%% Signal:{snr} Hops:{hops} {ack} ({online}/{total})
```

**Key Settings:**
- `want_ack` - Enable/disable delivery confirmation (on/off, **recommended: on**)
- `ack_wait_time` - Seconds to wait before sending ACK confirmation (default 30, recommended 30-60 for mesh)
- `ack_retry_timeout` - Seconds to wait before retrying on no ACK (default 60)
- `mesh_send_mode` - Message routing mode:
  - `mesh` (default) - Messages route through the mesh network (hop_limit=3)
  - `direct` - Messages only sent to direct neighbors (hop_limit=0)
- `pki_encrypted` - Enable/disable PKI public key encryption (on/off, default off)
- `channel_index` - Channel to send on (0 = primary/private, prevents LongFast broadcast)

### ACK Wait Time Configuration

**New in v2.0**: The `ack_wait_time` setting controls how long to wait before sending an ACK confirmation message back to the sender.

**Why this matters for mesh networks:**
- Mesh messages take time to propagate through multiple hops
- ACKs need to travel back through the mesh to reach the original sender
- 10 seconds (old default) was too short for mesh networks
- 30 seconds (new default) gives sufficient time for mesh routing

**Recommended values:**
- **Mesh networks**: 30-60 seconds (default 30)
- **Direct communication**: 10-20 seconds
- **Very slow mesh**: 45-60 seconds
- **Co-located nodes**: 10-15 seconds

**Configure via menu**: Options → Change ACK Wait Time (option 9)

### Channel Index Configuration

**New in v2.0**: The `channel_index` setting prevents your messages from appearing on public shared channels like LongFast.

**How it works:**
- `channel_index = 0` - Send on primary channel (typically private/encrypted)
- `channel_index = 1-7` - Send on secondary channels (may be public)

**Why use channel 0:**
- Keeps weather messages private to your mesh
- Prevents broadcast on shared LongFast channel
- Only nodes with access to your primary channel see messages
- Works with both channel encryption and PKI encryption

**Default:** `channel_index = 0` (recommended)

### Mesh vs Direct Sending

The `mesh_send_mode` setting controls how messages are routed:

**Mesh Mode** (`mesh_send_mode = mesh`):
- Messages can hop through up to 3 intermediate nodes
- Best for reaching distant nodes not in direct radio range
- Messages are forwarded by intermediate mesh nodes
- Higher reliability for reaching remote nodes
- Default and recommended for most deployments

**Direct Mode** (`mesh_send_mode = direct`):
- Messages only sent to direct neighbors (single hop)
- No mesh routing or forwarding
- Faster delivery, lower network congestion
- Only works if target node is in direct radio range
- Useful for local deployments or reducing mesh traffic

Example use cases:
- Use **mesh mode** when sender and receiver are far apart
- Use **direct mode** when both nodes are at same location
- Use **direct mode** to reduce mesh congestion in dense networks

### PKI Public Key Encryption

**Enhanced in v2.0** with auto-scan feature and improved key management.

The `pki_encrypted` setting enables end-to-end encryption using public key infrastructure (PKI) instead of shared channel keys.

**Channel Encryption (Default):**
- Uses shared Pre-Shared Keys (PSK) configured in Meshtastic channels
- Anyone with the channel key can decrypt messages
- Simpler to configure, works out of the box
- Suitable for trusted mesh networks

**PKI Encryption** (`pki_encrypted = on`):
- End-to-end encryption using recipient's public key
- Only the recipient (with matching private key) can decrypt
- More secure for sensitive data
- Requires obtaining and configuring public keys for each node

**To enable PKI encryption (Method 1 - Auto-scan):**

1. **Enable PKI on your nodes** (Meshtastic CLI or app):
```bash
meshtastic --set security.public_key true
```

2. **Use the built-in scanner** (Options menu → Scan/Update Public Keys):
   - Automatically discovers and imports public keys from nodes
   - Saves keys to `config.ini` in base64 format
   - Handles missing keys gracefully (warns if PKI not enabled on node)
   - Option to update existing keys or add new ones only

3. **Enable PKI encryption** (Options menu → Toggle PKI Encryption)

**To enable PKI encryption (Method 2 - Manual):**

1. **Obtain public keys** from target nodes:
```bash
# On each target node, get its public key
meshtastic --info
# Look for "Public Key" field in output
```

2. **Add public keys to config.ini:**
```ini
[settings]
pki_encrypted = on

[public_keys]
yang = bOatKxov+G+kjVIzYP1bLV0sF1kktpVrhAMGwsMttVA=
ying = 0d5PCyDP0yUCEzH0AIcx1UUGoifdnuiMHRLEURNJTxM=
```

3. **Messages will now be PKI encrypted** when sent to nodes with configured public keys

**Note:** PKI encryption is independent of mesh routing. You can use:
- Mesh routing + channel encryption (default)
- Mesh routing + PKI encryption
- Direct routing + channel encryption
- Direct routing + PKI encryption

**When to use PKI:**
- Sending sensitive weather data or telemetry
- Communicating with untrusted mesh participants
- Compliance requirements for data encryption
- When you want to ensure only specific recipients can read messages

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

Messages use customizable templates defined in `config.ini`. The default template (template1) formats messages in 3 lines optimized for Meshtastic displays:

```
10/31 14:20 (6/114)
T: 83F 6.2 SNR 0 HOP A
H: 25% 14:20:31 (6/114)
```

- **Line 1**: Date, time, (online nodes/total nodes)
- **Line 2**: Temperature in Fahrenheit, SNR (signal strength), HOP count, **A/U (ACK status)**
- **Line 3**: Humidity percentage, military time, (online/total nodes)

### New in v2.0: ACK Status Indicator

The `{ack}` placeholder shows the acknowledgment status of the previous message:
- **A** - Message was acknowledged (delivery confirmed)
- **U** - Message was not acknowledged (delivery uncertain)
- **(blank)** - No previous message or ACK tracking disabled

This helps you see at a glance if your messages are getting through!

### Message Delivery Confirmation

**Enhanced in v2.0** with verbose tracking and configurable timing.

The system uses Meshtastic's ACK/NAK system to verify message delivery with automatic retry capability:

**Sender Display:**
```
============================================================
📤 To: yang
Sent: 16:24:27
Ack : 16:24:29
SNR : 7.0
✓ yang
============================================================
```

**Real-time ACK Notification:**
When an ACK is received (even during sensor reading), you'll see:
```
✓ ACK received from yang at 16:24:29
```

**Verbose ACK Tracking (New in v2.0):**
Detailed console output helps troubleshoot ACK issues:
```
[ACK] ACK tracking enabled - callback registered
[ACK] Message 123456789 sent to ying, waiting for ACK...
[ACK CALLBACK] Received packet - request_id: 123456789, from_node: 2658555560, error: NONE
[ACK] Processing response for message 123456789 to ying
✓ REAL ACK received from ying at 16:24:29!
[ACK] Confirmation message will be sent in 30 seconds
```

**ACK Confirmation Messages (New in v2.0):**
After receiving an ACK, the sender waits 30 seconds (configurable) then sends a confirmation:
```
yang ack
11/03 16:24:59
SNR: 7.2
```

This lets the receiver know their ACK was received!

**LED Feedback (Heltec V3):**
- **1 quick blink** (0.5s on) - Message sent/queued
- **Slow pulse** (0.3s on, 0.7s off, repeats) - Waiting for ACK
- **3 long blinks** (1s on, 0.5s off between) - ACK received from target
- **5 quick flashes** (0.1s) - NAK received (delivery failed)
- **LED off** - No acknowledgment after timeout

**Status Messages:**
- `✓ yang` - ACK received, delivery confirmed
- `✗ NAK from: yang` - Delivery failed
- `⏳ Pending response from: yang` - Awaiting acknowledgment
- `[ACK] Still waiting for ACK from 1 node(s)` - Verbose status during wait

**Automatic Retry:**
If no ACK is received within the timeout period (default 60 seconds, configurable via `ack_retry_timeout` in config.ini), the program automatically retries sending the message. The program continues normal operation (sensor readings) while waiting for the retry timeout.

## SNR Statistics & Analytics

**New in v2.0**: Track signal quality over time with persistent all-time statistics.

### Viewing SNR Statistics

From the Reports menu, select "SNR Statistics" to see:

```
SNR STATISTICS (Signal-to-Noise Ratio) - ALL TIME
================================================================================
Min SNR = Cutoff (weakest signal ever seen)
Max SNR = Optimal (strongest signal ever seen)

Node Name            Min (Cutoff)    Max (Optimal)   Average    Count     
--------------------------------------------------------------------------------
yang                 -12.5 dB        8.2 dB          -2.3 dB    247       
  Period: 2025-11-03 08:15 to 2025-11-03 18:30 (span: 10h 15m)
  Recent trend: -3.2, -1.8, -2.5, 0.5, -3.1, 1.2, -0.8, -2.3, -4.1, -1.5

Total nodes tracked: 1
Statistics file: snr_stats.json

Note: These are all-time statistics since last reset.
```

### Features

- **All-time tracking** - Statistics persist across restarts via `snr_stats.json`
- **Min/Max SNR** - Identify cutoff (weakest) and optimal (strongest) signals
- **Running average** - Overall signal quality indicator
- **Sample count** - Number of readings collected
- **Time period** - First seen, last seen, and total duration
- **Recent trend** - Last 10 SNR values to spot signal degradation
- **Reset capability** - Clear statistics with double confirmation

### Use Cases

1. **Identify weak signals**: Low min SNR shows communication is possible but marginal
2. **Optimal conditions**: High max SNR shows best-case signal strength
3. **Trend analysis**: Recent values show if signal is improving or degrading
4. **Network planning**: Use stats to optimize node placement
5. **Troubleshooting**: Compare min/max/avg to diagnose connectivity issues

### Resetting Statistics

At the bottom of the SNR Statistics report:
```
Reset all statistics? (y/n): y
Are you sure? This cannot be undone (y/n): y

✓ SNR statistics have been reset
```

This clears all historical data and starts fresh tracking.

### Customizing Message Templates

Edit `config.ini` to customize the message format or switch between templates:

```ini
[settings]
message_template = template1  # Choose: template1, template2, or template3

[message_templates]
# Create your own templates using these placeholders:
# {date}, {time}, {time_detail}, {online}, {total}, {temp}, {humidity}, {snr}, {hops}
# Use \n for line breaks and %% for percent signs

template1 = {date} {time} ({online}/{total})\nT: {temp}F {snr} SNR {hops} HOP\nH: {humidity}%% {time_detail} ({online}/{total})

# Add your own custom templates here
```

**Available Placeholders:**
- `{date}` - MM/DD format
- `{time}` - HH:MM format  
- `{time_detail}` - HH:MM:SS format
- `{online}` - Online node count
- `{total}` - Total node count
- `{temp}` - Temperature (integer)
- `{humidity}` - Humidity (integer)
- `{snr}` - Signal strength (1 decimal)
- `{hops}` - Hop count
- `{ack}` - ACK status indicator: **A** (ack), **U** (unack), or blank **(New in v2.0)**

Three templates are included:
- **template1** (default): 3-line compact format with signal info and ACK status
- **template2**: 4-line detailed format with ACK status
- **template3**: 2-line simple format with ACK status

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

### Main Menu

1. **Start Sending Messages** - Begin reading sensor and sending to mesh
2. **Stop Sending Messages** - Return to main menu
3. **Options** - Configure settings (see Options Menu below)
4. **Reports** - View statistics and analytics (see Reports Menu below)
5. **View Sample Message** - Preview message format
6. **Exit** - Quit the program

### Options Menu

1. **Change Message Target Node** - Select which node(s) to send to
2. **Change Update Interval** - Set seconds between messages (default 60)
3. **Change USB Reconnect Interval** - Set Meshtastic reconnect delay (default 10)
4. **Change Log Retention Days** - Set CSV log retention period (default 7)
5. **Change Mesh Routing Mode** - Switch between mesh and direct modes
6. **Toggle Message ACK** - Enable/disable delivery confirmation
7. **Toggle PKI Encryption** - Enable/disable public key encryption
8. **Scan/Update Public Keys** - Auto-discover and import public keys **(New in v2.0)**
9. **Change ACK Wait Time** - Set delay for ACK confirmation messages **(New in v2.0)**
10. **Back to Main Menu**

### Reports Menu

1. **List of Nodes Seen** - View all nodes heard on the mesh with last heard times
2. **SNR Statistics** - View all-time signal quality analytics **(New in v2.0)**
3. **Back to Main Menu**

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

**ACK issues (New in v2.0 troubleshooting):**
- Check verbose ACK output: `[ACK]` messages show callback activity
- Verify `want_ack = on` in config.ini
- Check ACK callback is registered: Look for "ACK tracking enabled" at startup
- Try increasing `ack_wait_time` to 60 seconds for slow mesh networks
- Verify target node is responding: Check if you receive any ACK callbacks

**Messages appearing on LongFast (New in v2.0):**
- Set `channel_index = 0` in config.ini to use primary channel
- Restart the program after changing channel setting
- Verify with another node that messages are on correct channel

**PKI encryption not working (New in v2.0):**
- Verify `pki_encrypted = on` in config.ini
- Use Options menu → Scan/Update Public Keys to import keys
- Check nodes have PKI enabled: `meshtastic --set security.public_key true`
- Fallback: If no public key, messages use channel encryption automatically

**SNR statistics not updating (New in v2.0):**
- Statistics only update when messages are received with SNR values
- Check `snr_stats.json` file exists and is writable
- Stats auto-save every 10 updates
- Manual save on clean exit (Ctrl+C gracefully)

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

Requires Meshtastic firmware 2.0+ for full PKI encryption support.

## Version History

### Version 2.0 (November 2025)
- **ACK System Enhancements**
  - Verbose ACK tracking with detailed console output
  - ACK status indicators in messages ({ack} placeholder)
  - Configurable ACK wait time (default 30s for mesh networks)
  - ACK confirmation messages sent back to sender
  - Improved ACK callback registration and error handling
  
- **PKI Encryption Features**
  - Auto-scan and import public keys from mesh nodes
  - Manual public key entry support
  - Per-node PKI encryption with fallback to channel encryption
  - Improved error handling and user feedback
  
- **Channel Control**
  - Configurable channel index to prevent LongFast broadcast
  - Send to specific channels (default: primary/private channel 0)
  
- **SNR Analytics**
  - All-time SNR statistics tracking per node
  - Min (cutoff), max (optimal), and average SNR values
  - Time period tracking (first seen, last seen, duration)
  - Recent trend analysis (last 10 values)
  - Persistent storage in snr_stats.json
  - Reset capability with double confirmation
  
- **User Experience Improvements**
  - Verbose debugging output for troubleshooting
  - Cleaner countdown display (removed confusing retry messages)
  - Enhanced menu system with new options
  - Improved error messages and user guidance

### Version 1.0 (Initial Release)
- Basic temperature/humidity sensor reading
- Meshtastic message sending
- CSV logging
- Interactive menu system
- ACK/NAK support
- Multi-node configuration
- Customizable templates

## License

MIT License - Free to use and modify.

## Contributing

Issues and pull requests welcome at: https://github.com/iainonline/meshtastic_weather_works
