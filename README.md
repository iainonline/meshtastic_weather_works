# DHT22 Temperature & Humidity Sensor for Raspberry Pi 5

Python project to read temperature and humidity data from a DHT22 sensor connected to a Raspberry Pi 5 and send updates to a Meshtastic node.

## Features

- Reads temperature (°F) and humidity (%) from DHT22 sensor
- Displays readings every 2 seconds
- Sends private messages to configured Meshtastic node every 60 seconds
- Configurable target node via config file

## Hardware Requirements

- Raspberry Pi 5 (or Pi Zero 2 W - same pinout)
- DHT22 (AM2302) Temperature & Humidity Sensor
- 3 female-to-female jumper wires
- (Optional) 10kΩ pull-up resistor if not built into sensor module
- Meshtastic device connected via USB (for messaging feature)

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
python3 -m venv venv
source venv/bin/activate
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

The required packages are:
- `adafruit-circuitpython-dht` - Library for DHT sensors
- `RPi.GPIO` - GPIO library for Raspberry Pi
- `meshtastic` - Meshtastic Python API for messaging

### 3. Configure Meshtastic

Edit `config.ini` and set your target node number:

```ini
[meshtastic]
# Node number to send messages to (e.g., !12345678)
target_node = !12345678

# Message update interval in seconds
update_interval = 60
```

To find your target node number, use the Meshtastic CLI:
```bash
meshtastic --info
```

### 4. Run the Script

```bash
python dht22_reader.py
```

The script will:
- Read and display temperature (°F) and humidity every 2 seconds
- Send private messages to the configured Meshtastic node every 60 seconds

## Usage

```python
#!/usr/bin/env python3
import board
import adafruit_dht

# Initialize DHT22 on GPIO4
dht_device = adafruit_dht.DHT22(board.D4)

# Read sensor
temperature_c = dht_device.temperature
humidity = dht_device.humidity

print(f"Temp: {temperature_c}°C, Humidity: {humidity}%")
```

## Customization

### Change Meshtastic Update Interval

Edit `config.ini` to change how often messages are sent:

```ini
update_interval = 120  # Send every 2 minutes instead
```

### Change GPIO Pin

Edit `dht22_reader.py` and modify the `DHT_PIN` variable:

```python
# Use GPIO18 instead of GPIO4
DHT_PIN = board.D18
```

Available GPIO pins: D4, D18, D22, D23, D24, etc.

### Adjust Reading Interval

Modify the `time.sleep()` value in the main loop:

```python
time.sleep(5.0)  # Read every 5 seconds
```

Note: DHT22 has a minimum 2-second sampling period.

## Troubleshooting

**Import errors**: Make sure the virtual environment is activated and dependencies are installed.

**RuntimeError during reading**: DHT sensors can be temperamental. The script automatically retries on the next reading cycle.

**No data**: Check wiring connections, especially DATA and GND pins.

**Meshtastic connection failed**: 
- Ensure Meshtastic device is connected via USB
- Check device permissions: `sudo usermod -a -G dialout $USER` (logout/login required)
- Verify with: `meshtastic --info`
- Script will continue without messaging if Meshtastic unavailable

**Permission errors**: You may need to run with sudo on some systems:
```bash
sudo venv/bin/python dht22_reader.py
```

## Compatibility

This project is designed for Raspberry Pi 5 but works on:
- Raspberry Pi 5
- Raspberry Pi Zero 2 W (same GPIO pinout)
- Raspberry Pi 4
- Raspberry Pi 3/3+
- Any Raspberry Pi with 40-pin GPIO header

## License

MIT License - Free to use and modify.
