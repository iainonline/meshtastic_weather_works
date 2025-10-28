#!/usr/bin/env python3
"""
DHT22 Temperature and Humidity Sensor Reader for Raspberry Pi 5
Reads temperature and humidity data from a DHT22 sensor connected to GPIO4.
Sends data to Meshtastic node every 60 seconds via USB.
"""

import time
import board
import adafruit_dht
import meshtastic
import meshtastic.serial_interface
import configparser
import logging
from datetime import datetime
import signal
from contextlib import contextmanager

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('dht22_meshtastic.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Timeout handler for sensor reads
class TimeoutException(Exception):
    pass

@contextmanager
def time_limit(seconds):
    def signal_handler(signum, frame):
        raise TimeoutException("Timed out!")
    signal.signal(signal.SIGALRM, signal_handler)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)

# Initialize the DHT22 sensor on GPIO4 (Pin 7)
# For other GPIO pins, use: board.D18, board.D22, board.D23, etc.
DHT_PIN = board.D4
dht_device = adafruit_dht.DHT22(DHT_PIN)

# Load configuration
config = configparser.ConfigParser()
config_file = 'config.ini'
if not config.read(config_file):
    logger.error(f"Failed to read {config_file}. Using defaults.")
    TARGET_NODE = "!12345678"
    TARGET_NODE_INT = None
    UPDATE_INTERVAL = 60
else:
    # Get Meshtastic settings from config
    TARGET_NODE = config.get('meshtastic', 'target_node')
    UPDATE_INTERVAL = config.getint('meshtastic', 'update_interval')
    
    # Convert target node to integer (handle hex format like !9e7656a8)
    if TARGET_NODE.startswith('!'):
        TARGET_NODE_INT = int(TARGET_NODE[1:], 16)
        logger.info(f"Loaded configuration from {config_file}")
        logger.info(f"Target node from config: {TARGET_NODE} (decimal: {TARGET_NODE_INT})")
    else:
        TARGET_NODE_INT = int(TARGET_NODE)
        logger.info(f"Loaded configuration from {config_file}")
        logger.info(f"Target node from config: {TARGET_NODE_INT}")

# Initialize Meshtastic interface
meshtastic_interface = None
meshtastic_connected = False

def init_meshtastic():
    """Initialize Meshtastic serial interface via USB."""
    global meshtastic_interface, meshtastic_connected
    try:
        logger.info("Attempting to connect to Meshtastic device via USB...")
        meshtastic_interface = meshtastic.serial_interface.SerialInterface()
        meshtastic_connected = True
        logger.info("Meshtastic interface initialized successfully")
        return True
    except Exception as e:
        logger.error(f"Failed to initialize Meshtastic: {e}")
        meshtastic_interface = None
        meshtastic_connected = False
        return False

def check_and_reconnect_meshtastic():
    """Check if Meshtastic is connected and attempt to reconnect if not."""
    global meshtastic_interface, meshtastic_connected
    
    if not meshtastic_connected or meshtastic_interface is None:
        logger.info("Attempting to reconnect to Meshtastic...")
        return init_meshtastic()
    
    return True

def send_meshtastic_message(message):
    """Send a private message to the configured node. Handles disconnections."""
    global meshtastic_interface, meshtastic_connected
    try:
        if meshtastic_interface:
            logger.info(f"Attempting to send message to node {TARGET_NODE}...")
            logger.info(f"Message content: {message}")
            
            # Send the message using integer node number
            result = meshtastic_interface.sendText(message, destinationId=TARGET_NODE_INT)
            
            logger.info(f"Send result: {result}")
            logger.info(f"✓ Message sent to {TARGET_NODE} (decimal: {TARGET_NODE_INT})")
            logger.info(f"  Message: {message}")
            logger.info(f"  Message: {message}")
            
            return True
        else:
            logger.warning("Meshtastic interface not available")
            return False
    except Exception as e:
        logger.error(f"Error sending message (USB may be disconnected): {e}")
        # Mark as disconnected and clean up
        meshtastic_connected = False
        try:
            if meshtastic_interface:
                meshtastic_interface.close()
        except:
            pass
        meshtastic_interface = None
        logger.warning("Meshtastic marked as disconnected. Will retry on next send.")
        return False

def read_sensor():
    """
    Read temperature and humidity from the DHT22 sensor with timeout.
    
    Returns:
        tuple: (temperature_c, humidity) or (None, None) if reading failed
    """
    try:
        logger.debug("Attempting to read from DHT22...")
        
        # Use timeout to prevent hanging
        with time_limit(5):
            temperature_c = dht_device.temperature
            humidity = dht_device.humidity
        
        logger.debug(f"DHT22 returned: {temperature_c}°C, {humidity}%")
        return temperature_c, humidity
    
    except TimeoutException:
        logger.warning("DHT22 reading timed out after 5 seconds")
        return None, None
    
    except RuntimeError as error:
        # DHT sensors can be finicky and may fail occasionally
        # This is normal, just retry on the next reading
        logger.warning(f"DHT22 reading error: {error.args[0]}")
        return None, None
    
    except Exception as error:
        logger.error(f"Unexpected sensor error: {error}")
        return None, None


def main():
    """
    Main function to continuously read and display sensor data.
    Sends data to Meshtastic node via USB every 60 seconds.
    Automatically reconnects if USB is disconnected.
    """
    global meshtastic_connected
    
    logger.info("=" * 50)
    logger.info("DHT22 Sensor Reader for Raspberry Pi 5")
    logger.info("=" * 50)
    logger.info(f"Sensor connected to GPIO4 (Physical Pin 7)")
    logger.info(f"Meshtastic target node: {TARGET_NODE}")
    logger.info(f"Update interval: {UPDATE_INTERVAL} seconds")
    logger.info("Press Ctrl+C to exit\n")
    
    # Read sensor first to verify it's working
    logger.info("Testing DHT22 sensor before initializing Meshtastic...")
    logger.info("Waiting 3 seconds for sensor to stabilize...")
    time.sleep(3)
    
    # Try up to 99 times to get initial reading
    test_temp, test_hum = None, None
    for attempt in range(99):
        logger.info(f"Sensor test attempt {attempt + 1}/99...")
        test_temp, test_hum = read_sensor()
        if test_temp is not None and test_hum is not None:
            test_temp_f = test_temp * (9 / 5) + 32
            logger.info(f"Sensor test successful: {test_temp_f:.1f}°F, {test_hum:.1f}%")
            break
        else:
            if attempt < 98:
                logger.warning(f"Attempt {attempt + 1} failed, waiting 2 seconds...")
                time.sleep(2)
    
    if test_temp is None:
        logger.warning("All sensor test attempts failed, but continuing anyway...")
        logger.warning("Check wiring: VCC->Pin1(3.3V), DATA->Pin7(GPIO4), GND->Pin6")
    
    # Initialize Meshtastic
    logger.info("Initializing Meshtastic...")
    init_meshtastic()
    logger.info("Starting main sensor reading loop...")
    
    last_message_time = 0
    last_temperature_f = None
    last_humidity = None
    
    try:
        while True:
            logger.debug("Reading sensor...")
            # Read sensor data
            temperature_c, humidity = read_sensor()
            logger.debug(f"Sensor read complete: temp={temperature_c}, humidity={humidity}")
            
            current_time = time.time()
            
            if temperature_c is not None and humidity is not None:
                # Convert to Fahrenheit
                temperature_f = temperature_c * (9 / 5) + 32
                
                # Store last valid readings
                last_temperature_f = temperature_f
                last_humidity = humidity
                
                # Display readings
                logger.info(f"Temperature: {temperature_f:.1f}°F")
                logger.info(f"Humidity: {humidity:.1f}%")
                print("-" * 50)
                
                # Send to Meshtastic with every reading (three lines for Heltec display)
                timestamp = time.strftime("%m/%d %H:%M")
                message = f"{timestamp}\nTemp: {temperature_f:.1f}°F\nHum : {humidity:.1f}%"
                
                # Try to reconnect if disconnected
                if not meshtastic_connected:
                    logger.info("Meshtastic disconnected. Attempting to reconnect...")
                    check_and_reconnect_meshtastic()
                
                # Send message if connected
                if meshtastic_connected:
                    send_meshtastic_message(message)
                else:
                    logger.warning("Meshtastic not available. Skipping message send.")
                    
            else:
                # Display * when sensor fails, show last known reading
                if last_temperature_f is not None and last_humidity is not None:
                    logger.warning(f"* Temperature: {last_temperature_f:.1f}°F (last reading)")
                    logger.warning(f"* Humidity: {last_humidity:.1f}% (last reading)")
                else:
                    logger.warning("* No sensor data available yet")
                print("-" * 50)
            
            # Wait 60 seconds between readings (one reading per minute)
            time.sleep(60.0)
    
    except KeyboardInterrupt:
        logger.info("\n\nExiting program...")
    
    except Exception as e:
        logger.error(f"Unexpected error in main loop: {e}", exc_info=True)
    
    finally:
        # Clean up
        logger.info("Cleaning up resources...")
        try:
            dht_device.exit()
            logger.info("DHT22 sensor closed")
        except Exception as e:
            logger.error(f"Error closing DHT22: {e}")
        
        if meshtastic_interface:
            try:
                meshtastic_interface.close()
                logger.info("Meshtastic interface closed")
            except Exception as e:
                logger.error(f"Error closing Meshtastic: {e}")
        
        logger.info("Sensor cleanup complete.")


if __name__ == "__main__":
    main()
