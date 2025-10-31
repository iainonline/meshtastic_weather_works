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
from datetime import datetime, timedelta
import signal
from contextlib import contextmanager
import sys
import io
import select
import termios
import tty
import csv
import os
import atexit

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

# Global flag for graceful shutdown
shutdown_requested = False
menu_requested = False

# Keyboard input handler
def check_for_quit_or_menu():
    """Check if 'q' or 'm' key has been pressed. Returns 'q', 'm', or None."""
    global shutdown_requested, menu_requested
    if sys.stdin in select.select([sys.stdin], [], [], 0)[0]:
        char = sys.stdin.read(1)
        if char.lower() == 'q':
            shutdown_requested = True
            return 'q'
        elif char.lower() == 'm':
            menu_requested = True
            return 'm'
    return None

def check_for_quit():
    """Check if 'q' key has been pressed."""
    result = check_for_quit_or_menu()
    return result == 'q'

def cleanup_and_exit():
    """Cleanup resources and exit gracefully."""
    global meshtastic_interface, dht_device
    logger.info("\n" + "="*50)
    logger.info("Shutting down gracefully...")
    logger.info("="*50)
    
    # Close Meshtastic interface
    if meshtastic_interface:
        try:
            logger.info("Closing Meshtastic interface...")
            meshtastic_interface.close()
            logger.info("✓ Meshtastic interface closed")
        except Exception as e:
            logger.warning(f"Error closing Meshtastic: {e}")
    
    # Clean up DHT sensor
    if dht_device:
        try:
            logger.info("Cleaning up DHT22 sensor...")
            # Suppress any internal errors during cleanup
            try:
                dht_device.exit()
            except (ValueError, RuntimeError):
                # Ignore list removal errors - sensor already cleaned up
                pass
            logger.info("✓ DHT22 sensor cleaned up")
        except Exception as e:
            logger.debug(f"DHT22 cleanup note: {e}")
    
    logger.info("Goodbye!")
    sys.exit(0)

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

# Try to cleanup any existing GPIO claims first
import subprocess
try:
    # Use gpioset to briefly claim and release the line to clear any stuck state
    subprocess.run(['gpioset', '-m', 'time', '-s', '1', 'gpiochip4', '4=0'], 
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2)
except:
    pass  # Ignore errors, this is just a best-effort cleanup

# Capture stderr to detect GPIO errors
import sys
import io
old_stderr = sys.stderr
sys.stderr = io.StringIO()

try:
    dht_device = adafruit_dht.DHT22(DHT_PIN)
    error_output = sys.stderr.getvalue()
finally:
    sys.stderr = old_stderr

# Check for GPIO initialization error
if "Unable to set line" in error_output:
    print("\n" + "="*60)
    print("ERROR: GPIO4 is already in use or cannot be accessed!")
    print("="*60)
    print("\nThis usually means another process is using GPIO4.")
    print("Please run this command to stop any existing instances:")
    print("\n    sudo pkill -f ws4m\n")
    print("Then try running the script again.")
    print("="*60 + "\n")
    sys.exit(1)

# Register cleanup handler for proper GPIO release on exit
def cleanup_gpio_on_exit():
    """Ensure GPIO is properly released on program exit."""
    global dht_device
    try:
        if dht_device:
            try:
                dht_device.exit()
            except (ValueError, RuntimeError):
                # Ignore list removal errors - already cleaned up
                pass
    except:
        pass

atexit.register(cleanup_gpio_on_exit)

# Load configuration
config = configparser.ConfigParser()
config_file = 'config.ini'

# Global configuration variables
NODES = {}
SELECTED_NODE_NAME = None
TARGET_NODE_INT = None
UPDATE_INTERVAL = 60
AUTO_BOOT_TIMEOUT = 10
USB_RECONNECT_INTERVAL = 10
LOG_FILE = 'meshtastic_log.csv'
AUTO_SAVE_INTERVAL = 300
RETENTION_DAYS = 7
MESSAGE_TEMPLATE = 'template1'
MESSAGE_TEMPLATES = {}

def load_config():
    """Load configuration from config.ini file."""
    global NODES, SELECTED_NODE_NAME, TARGET_NODE_INT, UPDATE_INTERVAL
    global AUTO_BOOT_TIMEOUT, USB_RECONNECT_INTERVAL, LOG_FILE, AUTO_SAVE_INTERVAL, RETENTION_DAYS
    global MESSAGE_TEMPLATE, MESSAGE_TEMPLATES
    
    if not config.read(config_file):
        logger.error(f"Failed to read {config_file}. Using defaults.")
        NODES = {'default': 12345678}
        SELECTED_NODE_NAME = 'default'
        TARGET_NODE_INT = 12345678
        MESSAGE_TEMPLATE = 'template1'
        MESSAGE_TEMPLATES = {}
        return
    
    # Load nodes
    if config.has_section('nodes'):
        NODES = {name: int(node_id) for name, node_id in config.items('nodes')}
    else:
        NODES = {'default': 12345678}
    
    # Load settings
    if config.has_section('settings'):
        SELECTED_NODE_NAME = config.get('settings', 'selected_node', fallback='yang')
        UPDATE_INTERVAL = config.getint('settings', 'update_interval', fallback=60)
        AUTO_BOOT_TIMEOUT = config.getint('settings', 'auto_boot_timeout', fallback=10)
        USB_RECONNECT_INTERVAL = config.getint('settings', 'usb_reconnect_interval', fallback=10)
        MESSAGE_TEMPLATE = config.get('settings', 'message_template', fallback='template1')
    else:
        SELECTED_NODE_NAME = 'yang'
        MESSAGE_TEMPLATE = 'template1'
    
    # Load message templates
    if config.has_section('message_templates'):
        # Decode escape sequences like \n
        MESSAGE_TEMPLATES = {name: template.encode().decode('unicode_escape') 
                            for name, template in config.items('message_templates')}
    else:
        # Default template if section missing
        MESSAGE_TEMPLATES = {
            'template1': '{date} {time} ({online}/{total})\nT: {temp}F {snr} snr/{hops} hop\nH: {humidity}% {time_detail}'
        }
    
    # Set target node
    TARGET_NODE_INT = NODES.get(SELECTED_NODE_NAME, list(NODES.values())[0])
    
    # Load logging settings
    if config.has_section('logging'):
        LOG_FILE = config.get('logging', 'log_file', fallback='meshtastic_log.csv')
        AUTO_SAVE_INTERVAL = config.getint('logging', 'auto_save_interval', fallback=300)
        RETENTION_DAYS = config.getint('logging', 'retention_days', fallback=7)
    
    logger.info(f"Loaded configuration from {config_file}")
    logger.info(f"Available nodes: {NODES}")
    logger.info(f"Selected node: {SELECTED_NODE_NAME} = {TARGET_NODE_INT}")
    logger.info(f"Message template: {MESSAGE_TEMPLATE}")

def save_config():
    """Save current configuration to config.ini file."""
    if not config.has_section('settings'):
        config.add_section('settings')
    
    config.set('settings', 'selected_node', SELECTED_NODE_NAME)
    
    with open(config_file, 'w') as f:
        config.write(f)
    logger.info(f"Configuration saved to {config_file}")

def show_node_selection_menu():
    """Display interactive menu for node selection."""
    global SELECTED_NODE_NAME, TARGET_NODE_INT
    
    print("\n" + "="*60)
    print("MESHTASTIC WEATHER STATION - MESSAGE TARGET SELECTION")
    print("="*60)
    print("\nThis Pi will SEND weather data TO the selected node")
    print("\nAvailable target nodes:")
    
    node_list = list(NODES.items())
    for i, (name, node_id) in enumerate(node_list, 1):
        current = " ← MESSAGE TARGET" if name == SELECTED_NODE_NAME else ""
        print(f"  {i}. {name}: {node_id}{current}")
    
    print(f"\n  Message target: {SELECTED_NODE_NAME} (ID: {TARGET_NODE_INT})")
    print("\nEnter node number to select target, or press Enter to continue")
    print("="*60)
    
    try:
        choice = input("\nYour choice: ").strip()
        if choice:
            idx = int(choice) - 1
            if 0 <= idx < len(node_list):
                SELECTED_NODE_NAME, TARGET_NODE_INT = node_list[idx]
                save_config()
                print(f"\n✓ Target selected: {SELECTED_NODE_NAME} (ID: {TARGET_NODE_INT})")
                print(f"  Weather data will be sent TO this node")
            else:
                print("\nInvalid selection. Using current target.")
    except (ValueError, KeyboardInterrupt):
        print("\nUsing current node.")
    
    print()

def show_main_menu():
    """Display main menu and return user choice."""
    global my_node_id
    
    # Determine connected (sender) node info
    sender_info = "Ready to Connect"
    if my_node_id:
        sender_name = next((name for name, node_id in NODES.items() if node_id == my_node_id), None)
        if sender_name:
            sender_info = f"{sender_name} (ID: {my_node_id})"
        else:
            sender_info = f"Unknown (ID: {my_node_id})"
    
    print("\n" + "="*60)
    print("MESHTASTIC WEATHER STATION - MAIN MENU")
    print("="*60)
    print(f"\nConnected Node (Sender): {sender_info}")
    print(f"Target Node (Receiver):  {SELECTED_NODE_NAME} (ID: {TARGET_NODE_INT})")
    print("\n" + "-"*60)
    print("\n1. Start Sending Messages")
    print("2. Stop Sending Messages")
    print("3. Options")
    print("4. Reports")
    print("5. View Sample Message")
    print("6. Exit")
    print("\n" + "="*60)
    
    try:
        choice = input("\nSelect option (1-6): ").strip()
        return choice
    except (KeyboardInterrupt, EOFError):
        return '6'

def show_main_menu_with_timeout():
    """Display main menu with 15-second timeout that auto-selects option 1."""
    global my_node_id
    
    # Determine connected (sender) node info
    sender_info = "Ready to Connect"
    if my_node_id:
        sender_name = next((name for name, node_id in NODES.items() if node_id == my_node_id), None)
        if sender_name:
            sender_info = f"{sender_name} (ID: {my_node_id})"
        else:
            sender_info = f"Unknown (ID: {my_node_id})"
    
    print("\n" + "="*60)
    print("MESHTASTIC WEATHER STATION - MAIN MENU")
    print("="*60)
    print(f"\nConnected Node (Sender): {sender_info}")
    print(f"Target Node (Receiver):  {SELECTED_NODE_NAME} (ID: {TARGET_NODE_INT})")
    print("\n" + "-"*60)
    print("\n1. Start Sending Messages")
    print("2. Stop Sending Messages")
    print("3. Options")
    print("4. Reports")
    print("5. View Sample Message")
    print("6. Exit")
    print("\n" + "="*60)
    print("\nAuto-starting option 1 in 15 seconds...")
    print("Select option (1-6) or wait: ", end='', flush=True)
    
    # Use select to wait for input with timeout
    old_settings = termios.tcgetattr(sys.stdin)
    try:
        tty.setcbreak(sys.stdin.fileno())
        start_time = time.time()
        user_input = ""
        last_remaining = 15
        
        while time.time() - start_time < 15:
            remaining = int(15 - (time.time() - start_time))
            
            # Check for input
            if sys.stdin in select.select([sys.stdin], [], [], 0.1)[0]:
                char = sys.stdin.read(1)
                if char == '\n':
                    print()
                    termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
                    return user_input.strip() if user_input.strip() else '1'
                elif char in '123456':
                    user_input = char
                    print(char, flush=True)
            
            # Update countdown every second - print on same line
            if remaining != last_remaining:
                last_remaining = remaining
                print(f"\rAuto-starting option 1 in {remaining} seconds...  Select option (1-6) or wait: {user_input}", end='', flush=True)
        
        # Timeout - auto-select option 1
        print("\n\n✓ Auto-starting option 1...")
        time.sleep(1)
        return '1'
        
    except (KeyboardInterrupt, EOFError):
        return '6'
    finally:
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)

def show_options_menu():
    """Display options submenu."""
    while True:
        print("\n" + "="*60)
        print("OPTIONS MENU")
        print("="*60)
        print("\n1. Change Message Target Node")
        print(f"2. Change Update Interval (current: {UPDATE_INTERVAL}s)")
        print(f"3. Change USB Reconnect Interval (current: {USB_RECONNECT_INTERVAL}s)")
        print(f"4. Change Log Retention Days (current: {RETENTION_DAYS} days)")
        print("5. Back to Main Menu")
        print("\n" + "="*60)
        
        try:
            choice = input("\nSelect option (1-5): ").strip()
            
            if choice == '1':
                show_node_selection_menu()
            elif choice == '2':
                change_update_interval()
            elif choice == '3':
                change_reconnect_interval()
            elif choice == '4':
                change_retention_days()
            elif choice == '5':
                break
            else:
                print("\nInvalid option. Please select 1-5.")
        except (KeyboardInterrupt, EOFError):
            break

def change_update_interval():
    """Change the update interval setting."""
    global UPDATE_INTERVAL
    try:
        new_interval = input(f"\nEnter new update interval in seconds (current: {UPDATE_INTERVAL}): ").strip()
        if new_interval:
            UPDATE_INTERVAL = int(new_interval)
            if not config.has_section('settings'):
                config.add_section('settings')
            config.set('settings', 'update_interval', str(UPDATE_INTERVAL))
            save_config()
            print(f"✓ Update interval changed to {UPDATE_INTERVAL} seconds")
    except ValueError:
        print("Invalid input. Update interval unchanged.")

def change_reconnect_interval():
    """Change the USB reconnect interval setting."""
    global USB_RECONNECT_INTERVAL
    try:
        new_interval = input(f"\nEnter new reconnect interval in seconds (current: {USB_RECONNECT_INTERVAL}): ").strip()
        if new_interval:
            USB_RECONNECT_INTERVAL = int(new_interval)
            if not config.has_section('settings'):
                config.add_section('settings')
            config.set('settings', 'usb_reconnect_interval', str(USB_RECONNECT_INTERVAL))
            save_config()
            print(f"✓ USB reconnect interval changed to {USB_RECONNECT_INTERVAL} seconds")
    except ValueError:
        print("Invalid input. Reconnect interval unchanged.")

def change_retention_days():
    """Change the log retention days setting."""
    global RETENTION_DAYS
    try:
        new_days = input(f"\nEnter new log retention days (current: {RETENTION_DAYS}): ").strip()
        if new_days:
            RETENTION_DAYS = int(new_days)
            if not config.has_section('logging'):
                config.add_section('logging')
            config.set('logging', 'retention_days', str(RETENTION_DAYS))
            save_config()
            print(f"✓ Log retention changed to {RETENTION_DAYS} days")
    except ValueError:
        print("Invalid input. Retention days unchanged.")

def show_menu():
    """Deprecated - kept for compatibility. Use show_node_selection_menu instead."""
    show_node_selection_menu()

def show_reports_menu():
    """Display reports menu."""
    while True:
        print("\n" + "="*60)
        print("REPORTS MENU")
        print("="*60)
        print("\n1. List of Nodes Seen")
        print("2. Back to Main Menu")
        print("\n" + "="*60)
        
        try:
            choice = input("\nSelect option (1-2): ").strip()
            
            if choice == '1':
                show_nodes_seen_report()
            elif choice == '2':
                break
            else:
                print("\nInvalid option. Please select 1-2.")
        except (KeyboardInterrupt, EOFError):
            break

def show_nodes_seen_report():
    """Display a report of all unique nodes seen in the log file."""
    if not os.path.exists(LOG_FILE):
        print(f"\n✗ Log file '{LOG_FILE}' not found.")
        input("\nPress Enter to continue...")
        return
    
    try:
        # Read all unique nodes from CSV
        nodes_seen = {}
        with open(LOG_FILE, 'r', newline='') as f:
            reader = csv.DictReader(f)
            for row in reader:
                node_id = row.get('Node_ID', 'Unknown')
                node_name = row.get('Node_Name', 'Unknown')
                timestamp = row.get('Timestamp', 'Unknown')
                last_heard = row.get('Last_Heard', 'Unknown')
                status = row.get('Status', 'Unknown')
                
                # Track unique nodes and their latest info
                if node_id not in nodes_seen or timestamp > nodes_seen[node_id]['timestamp']:
                    nodes_seen[node_id] = {
                        'name': node_name,
                        'timestamp': timestamp,
                        'last_heard': last_heard,
                        'status': status
                    }
        
        # Display report
        print("\n" + "="*70)
        print("NODES SEEN REPORT")
        print("="*70)
        print(f"\nTotal unique nodes: {len(nodes_seen)}")
        print("\n" + "-"*70)
        print(f"{'Node ID':<15} {'Name':<20} {'Last Heard':<20} {'Status':<10}")
        print("-"*70)
        
        for node_id, info in sorted(nodes_seen.items()):
            print(f"{node_id:<15} {info['name']:<20} {info['last_heard']:<20} {info['status']:<10}")
        
        print("="*70)
        input("\nPress Enter to continue...")
        
    except Exception as e:
        print(f"\n✗ Error reading log file: {e}")
        input("\nPress Enter to continue...")

def view_sample_message():
    """Display a sample message with current sensor readings."""
    print("\n" + "="*60)
    print("SAMPLE MESSAGE PREVIEW")
    print("="*60)
    
    # Get node stats (real or example)
    online_nodes, total_nodes = get_node_stats()
    if online_nodes is None:
        online_nodes, total_nodes = 5, 114  # Example values
    
    # Get target node info (real or example)
    snr, hops = get_target_node_info(TARGET_NODE_INT)
    if snr is None:
        snr, hops = -8.0, 2  # Example values
    
    # Sample temperature and humidity
    temperature_f = 81.0
    humidity = 29.0
    
    # Build sample message using template
    message = format_message(temperature_f, humidity, online_nodes, total_nodes, snr, hops)
    
    print("\nSample message that will be sent:")
    print("\n" + "-"*60)
    print(message)
    print("-"*60)
    print(f"\nUsing template: {MESSAGE_TEMPLATE}")
    print("\nAvailable templates:")
    for template_name in MESSAGE_TEMPLATES.keys():
        indicator = " (current)" if template_name == MESSAGE_TEMPLATE else ""
        print(f"  - {template_name}{indicator}")
    print("\nTo change template, edit config.ini [settings] message_template")
    print("="*60)
    input("\nPress Enter to continue...")

def wait_for_menu_or_timeout():
    """Wait for 'm' key press or timeout. Returns True if menu requested."""
    print("\n" + "="*60)
    print(f"Starting in {AUTO_BOOT_TIMEOUT} seconds with current settings...")
    print(f"Press 'm' for menu, or wait to auto-start")
    print("="*60 + "\n")
    
    old_settings = termios.tcgetattr(sys.stdin)
    try:
        tty.setcbreak(sys.stdin.fileno())
        start_time = time.time()
        
        while time.time() - start_time < AUTO_BOOT_TIMEOUT:
            if sys.stdin in select.select([sys.stdin], [], [], 0.1)[0]:
                char = sys.stdin.read(1)
                if char.lower() == 'm':
                    termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
                    return True
            remaining = int(AUTO_BOOT_TIMEOUT - (time.time() - start_time))
            if remaining != int(AUTO_BOOT_TIMEOUT - (start_time - start_time)):
                print(f"\rAuto-starting in {remaining}...  ", end='', flush=True)
        
        print("\n")
        return False
    finally:
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)

# Load initial configuration
load_config()

# Initialize Meshtastic interface
meshtastic_interface = None
meshtastic_connected = False
my_node_id = None  # Store the connected device's node ID

# CSV logging variables
csv_data_buffer = []
last_csv_save = time.time()

def init_csv_log():
    """Initialize CSV log file with headers if it doesn't exist."""
    if not os.path.exists(LOG_FILE):
        with open(LOG_FILE, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['Timestamp', 'Node_ID', 'Node_Name', 'Signal_Strength', 'SNR', 'Hops', 'Last_Heard', 'Status'])
        logger.info(f"Created CSV log file: {LOG_FILE}")

def log_node_data():
    """Log current node information to CSV buffer."""
    global csv_data_buffer, meshtastic_interface
    
    if not meshtastic_interface or not hasattr(meshtastic_interface, 'nodes'):
        return
    
    try:
        current_time = datetime.now()
        timestamp = current_time.strftime('%Y-%m-%d %H:%M:%S')
        
        for node_id, node_info in meshtastic_interface.nodes.items():
            # Extract node information
            node_name = node_info.get('user', {}).get('longName', 'Unknown') if isinstance(node_info.get('user'), dict) else 'Unknown'
            rssi = node_info.get('snr', 0)  # Signal strength
            snr = node_info.get('snr', 0)  # Signal-to-noise ratio
            hops = node_info.get('hopsAway', 0)
            last_heard = node_info.get('lastHeard', 0)
            
            # Determine status (online if heard in last 15 minutes)
            time_diff = time.time() - last_heard if last_heard else 999999
            status = 'online' if time_diff < 900 else 'offline'
            
            # Add to buffer
            csv_data_buffer.append([timestamp, node_id, node_name, rssi, snr, hops, 
                                   datetime.fromtimestamp(last_heard).strftime('%Y-%m-%d %H:%M:%S') if last_heard else 'Never', 
                                   status])
    
    except Exception as e:
        logger.warning(f"Error logging node data: {e}")

def save_csv_log():
    """Save CSV buffer to file and clear buffer."""
    global csv_data_buffer, last_csv_save
    
    if not csv_data_buffer:
        return
    
    try:
        # Append buffer to CSV file
        with open(LOG_FILE, 'a', newline='') as f:
            writer = csv.writer(f)
            writer.writerows(csv_data_buffer)
        
        logger.info(f"Saved {len(csv_data_buffer)} log entries to {LOG_FILE}")
        csv_data_buffer = []
        last_csv_save = time.time()
    
    except Exception as e:
        logger.error(f"Error saving CSV log: {e}")

def cleanup_old_logs():
    """Remove log entries older than RETENTION_DAYS."""
    if not os.path.exists(LOG_FILE):
        return
    
    try:
        cutoff_date = datetime.now() - timedelta(days=RETENTION_DAYS)
        
        # Read all rows
        with open(LOG_FILE, 'r', newline='') as f:
            reader = csv.reader(f)
            headers = next(reader)
            rows = list(reader)
        
        # Filter rows within retention period
        filtered_rows = []
        for row in rows:
            try:
                row_date = datetime.strptime(row[0], '%Y-%m-%d %H:%M:%S')
                if row_date >= cutoff_date:
                    filtered_rows.append(row)
            except (ValueError, IndexError):
                continue  # Skip malformed rows
        
        # Write back filtered data
        with open(LOG_FILE, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            writer.writerows(filtered_rows)
        
        removed = len(rows) - len(filtered_rows)
        if removed > 0:
            logger.info(f"Removed {removed} old log entries (older than {RETENTION_DAYS} days)")
    
    except Exception as e:
        logger.error(f"Error cleaning up old logs: {e}")

def get_node_stats():
    """Get online and total node count from Meshtastic interface."""
    global meshtastic_interface
    
    if not meshtastic_interface or not hasattr(meshtastic_interface, 'nodes'):
        logger.debug("get_node_stats: meshtastic_interface or nodes not available")
        return None, None
    
    try:
        total_nodes = len(meshtastic_interface.nodes)
        logger.debug(f"get_node_stats: Found {total_nodes} total nodes in meshtastic_interface.nodes")
        
        online_nodes = 0
        
        # Count nodes seen recently (within last 15 minutes)
        current_time = time.time()
        for node_id, node_info in meshtastic_interface.nodes.items():
            last_heard = node_info.get('lastHeard', 0)
            if last_heard and (current_time - last_heard) < 900:  # 15 minutes
                online_nodes += 1
        
        logger.debug(f"get_node_stats: {online_nodes} nodes heard in last 15 minutes")
        return online_nodes, total_nodes
    except Exception as e:
        logger.warning(f"Error getting node stats: {e}")
        return None, None

def get_target_node_info(target_node_id):
    """Get signal strength and hop count for a specific target node."""
    global meshtastic_interface
    
    if not meshtastic_interface or not hasattr(meshtastic_interface, 'nodes'):
        return None, None
    
    try:
        # Convert integer node ID to hex string format used by meshtastic
        # Node IDs are stored as hex strings like '!9e757a8c'
        node_hex = f"!{target_node_id:08x}"
        
        # Look up the target node in the nodes dictionary
        if node_hex in meshtastic_interface.nodes:
            node_info = meshtastic_interface.nodes[node_hex]
            
            # Get SNR (signal-to-noise ratio) as signal strength indicator
            snr = node_info.get('snr', None)
            
            # Get hops away
            hops = node_info.get('hopsAway', None)
            
            return snr, hops
        else:
            logger.debug(f"Target node {node_hex} not found in nodes list")
            return None, None
    except Exception as e:
        logger.warning(f"Error getting target node info: {e}")
        return None, None

def format_message(temperature_f, humidity, online_nodes=None, total_nodes=None, snr=None, hops=None):
    """Format message using the configured template."""
    global MESSAGE_TEMPLATE, MESSAGE_TEMPLATES
    
    # Get current timestamps
    date = time.strftime("%m/%d")
    time_now = time.strftime("%H:%M")
    time_detail = time.strftime("%H:%M:%S")
    
    # Format node stats
    if online_nodes is not None and total_nodes is not None:
        online = online_nodes
        total = total_nodes
    else:
        online = 0
        total = 0
    
    # Format signal info
    if snr is not None and hops is not None:
        snr_val = f"{snr:.1f}"
        hops_val = str(hops)
    else:
        snr_val = "--"
        hops_val = "--"
    
    # Get the template
    template = MESSAGE_TEMPLATES.get(MESSAGE_TEMPLATE, MESSAGE_TEMPLATES.get('template1', 
        '{date} {time} ({online}/{total})\\nT: {temp}F {snr} snr/{hops} hop\\nH: {humidity}% {time_detail}'))
    
    # Format the message
    message = template.format(
        date=date,
        time=time_now,
        time_detail=time_detail,
        online=online,
        total=total,
        temp=int(temperature_f),
        humidity=int(humidity),
        snr=snr_val,
        hops=hops_val
    )
    
    return message

def init_meshtastic():
    """Initialize Meshtastic serial interface via USB."""
    global meshtastic_interface, meshtastic_connected, my_node_id
    try:
        logger.info("Attempting to connect to Meshtastic device via USB...")
        meshtastic_interface = meshtastic.serial_interface.SerialInterface()
        meshtastic_connected = True
        
        # Get the connected device's node ID
        if hasattr(meshtastic_interface, 'myInfo') and meshtastic_interface.myInfo:
            my_node_id = meshtastic_interface.myInfo.my_node_num
            logger.info(f"Connected to Meshtastic device - My Node ID: {my_node_id}")
        else:
            my_node_id = None
            logger.warning("Could not determine connected device's node ID")
        
        logger.info("Meshtastic interface initialized successfully")
        return True
    except Exception as e:
        logger.error(f"Failed to initialize Meshtastic: {e}")
        logger.error("Please connect Meshtastic device via USB")
        meshtastic_interface = None
        meshtastic_connected = False
        my_node_id = None
        return False

def check_and_reconnect_meshtastic():
    """Check if Meshtastic is connected and attempt to reconnect if not."""
    global meshtastic_interface, meshtastic_connected
    
    if not meshtastic_connected or meshtastic_interface is None:
        logger.info("Attempting to reconnect to Meshtastic...")
        return init_meshtastic()
    
    return True

def send_meshtastic_message(message):
    """
    Send a private message to configured nodes. 
    If connected device matches a node in config, sends to all other nodes.
    Otherwise, sends to the selected target node.
    """
    global meshtastic_interface, meshtastic_connected, my_node_id
    
    if not meshtastic_interface:
        logger.warning("Meshtastic interface not available")
        return False
    
    try:
        # Determine which nodes to send to
        target_nodes = []
        
        # Check if our connected device is in the config
        if my_node_id and my_node_id in NODES.values():
            # Find which node we are
            my_node_name = next((name for name, node_id in NODES.items() if node_id == my_node_id), None)
            if my_node_name:
                logger.info(f"Connected device is '{my_node_name}' (ID: {my_node_id})")
                logger.info(f"Sending to all other configured nodes...")
                
                # Send to all nodes except ourselves
                for name, node_id in NODES.items():
                    if node_id != my_node_id:
                        target_nodes.append((name, node_id))
            else:
                # Send to selected node only
                target_nodes = [(SELECTED_NODE_NAME, TARGET_NODE_INT)]
        else:
            # Connected device not in config, send to selected node only
            logger.info(f"Sending to selected node: {SELECTED_NODE_NAME}")
            target_nodes = [(SELECTED_NODE_NAME, TARGET_NODE_INT)]
        
        # Send messages to all target nodes
        success_count = 0
        for name, node_id in target_nodes:
            try:
                logger.info(f"Attempting to send message to {name} (ID: {node_id})...")
                result = meshtastic_interface.sendText(message, destinationId=node_id)
                logger.info(f"✓ Message sent to {name} (ID: {node_id})")
                success_count += 1
            except Exception as e:
                logger.error(f"Failed to send to {name} (ID: {node_id}): {e}")
        
        logger.info(f"Message content: {message}")
        logger.info(f"Successfully sent to {success_count}/{len(target_nodes)} nodes")
        
        return success_count > 0
        
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
    Validates readings to ensure they are sensible.
    
    Returns:
        tuple: (temperature_c, humidity) or (None, None) if reading failed or invalid
    """
    try:
        logger.debug("Attempting to read from DHT22...")
        
        # Use timeout to prevent hanging
        with time_limit(5):
            temperature_c = dht_device.temperature
            humidity = dht_device.humidity
        
        # Validate readings are sensible
        # DHT22 range: -40 to 80°C, 0 to 100% humidity
        if temperature_c is not None and humidity is not None:
            if -40 <= temperature_c <= 80 and 0 <= humidity <= 100:
                logger.debug(f"DHT22 returned valid: {temperature_c}°C, {humidity}%")
                return temperature_c, humidity
            else:
                # Invalid data - discard silently (values outside sensor spec)
                logger.debug(f"DHT22 invalid data discarded: {temperature_c}°C, {humidity}%")
                return None, None
        
        # One or both values were None
        logger.debug(f"DHT22 returned None values: temp={temperature_c}, hum={humidity}")
        return None, None
    
    except TimeoutException:
        # Sensor took too long to respond - normal with DHT22
        logger.debug("DHT22 reading timed out after 5 seconds - resetting sensor")
        reset_sensor()
        return None, None
    
    except RuntimeError as error:
        # DHT sensors can be finicky and may fail occasionally
        # This is normal behavior - sensor communication errors happen
        logger.debug(f"DHT22 communication error: {error.args[0]} - resetting sensor")
        reset_sensor()
        return None, None
    
    except OSError as error:
        # GPIO errors like [Errno 22] Invalid argument
        logger.debug(f"GPIO error: {error} - resetting sensor")
        reset_sensor()
        return None, None
    
    except Exception as error:
        logger.error(f"Unexpected sensor error: {error}")
        reset_sensor()
        return None, None

def reset_sensor():
    """Reset the DHT22 sensor by reinitializing the GPIO."""
    global dht_device
    try:
        # Clean up existing sensor - suppress stderr to avoid GPIO warnings
        old_stderr = sys.stderr
        sys.stderr = io.StringIO()
        try:
            if dht_device:
                dht_device.exit()
            time.sleep(0.2)  # Longer pause to ensure GPIO is fully released
            
            # Reinitialize
            dht_device = adafruit_dht.DHT22(DHT_PIN)
        finally:
            sys.stderr = old_stderr
        
        logger.debug("Sensor reset complete")
    except Exception as e:
        logger.debug(f"Error during sensor reset: {e}")


def main():
    """
    Main function to continuously read and display sensor data.
    Sends data to Meshtastic node via USB every configured interval.
    Automatically reconnects if USB is disconnected.
    """
    global meshtastic_connected, last_csv_save
    
    # Track if this is the first menu display
    first_menu = True
    
    # Main menu loop
    while True:
        # Auto-start on first menu if no input after 15 seconds
        if first_menu:
            choice = show_main_menu_with_timeout()
            first_menu = False
        else:
            choice = show_main_menu()
        
        if choice == '1':
            # Start sending messages
            run_weather_station()
        elif choice == '2':
            print("\n✓ Messaging stopped. Returning to main menu...")
            time.sleep(1)
        elif choice == '3':
            # Options menu
            show_options_menu()
        elif choice == '4':
            # Reports menu
            show_reports_menu()
        elif choice == '5':
            # View sample message
            view_sample_message()
        elif choice == '6':
            # Exit
            print("\nExiting program...")
            cleanup_and_exit()
        else:
            print("\nInvalid option. Please select 1-6.")

def run_weather_station():
    """Run the weather station sensor reading and messaging loop."""
    global meshtastic_connected, last_csv_save
    
    logger.info("=" * 50)
    logger.info("DHT22 Sensor Reader for Raspberry Pi 5")
    logger.info("=" * 50)
    logger.info(f"Sensor connected to GPIO4 (Physical Pin 7)")
    logger.info(f"Meshtastic target node: {SELECTED_NODE_NAME} = {TARGET_NODE_INT}")
    logger.info(f"Update interval: {UPDATE_INTERVAL} seconds")
    logger.info(f"CSV logging to: {LOG_FILE} (retention: {RETENTION_DAYS} days)")
    logger.info("Press 'q' to quit or 'm' for menu\n")
    
    # Initialize CSV log
    init_csv_log()
    cleanup_old_logs()
    
    # Set terminal to non-blocking input mode
    old_settings = termios.tcgetattr(sys.stdin)
    try:
        tty.setcbreak(sys.stdin.fileno())
    except:
        logger.warning("Could not set terminal to cbreak mode. 'q' to quit may not work.")
    
    # Read sensor first to verify it's working
    logger.info("Testing DHT22 sensor before initializing Meshtastic...")
    logger.info("Waiting 3 seconds for sensor to stabilize...")
    time.sleep(3)
    
    # Try up to 999 times to get initial reading
    test_temp, test_hum = None, None
    attempt = 0
    while attempt < 999:
        test_temp, test_hum = read_sensor()
        if test_temp is not None and test_hum is not None:
            test_temp_f = test_temp * (9 / 5) + 32
            logger.info(f"Sensor test successful after {attempt + 1} attempts: {test_temp_f:.1f}°F, {test_hum:.1f}%")
            break
        else:
            attempt += 1
            if attempt < 999:
                time.sleep(0.5)
    
    if test_temp is None:
        logger.warning("All sensor test attempts failed, but continuing anyway...")
        logger.warning("Check wiring: VCC->Pin1(3.3V), DATA->Pin7(GPIO4), GND->Pin6")
    
    # Initialize Meshtastic
    logger.info("Initializing Meshtastic...")
    init_meshtastic()
    
    # Display messaging strategy
    if my_node_id and my_node_id in NODES.values():
        my_node_name = next((name for name, node_id in NODES.items() if node_id == my_node_id), None)
        other_nodes = [name for name, node_id in NODES.items() if node_id != my_node_id]
        logger.info("=" * 50)
        logger.info(f"Connected device '{my_node_name}' is in config")
        logger.info(f"Will send to ALL other nodes: {', '.join(other_nodes)}")
        logger.info("=" * 50)
    else:
        logger.info("=" * 50)
        logger.info(f"Will send to selected node: {SELECTED_NODE_NAME}")
        logger.info("=" * 50)
    
    logger.info("Starting main sensor reading loop...")
    
    last_message_time = 0
    last_temperature_f = None
    last_humidity = None
    last_reconnect_attempt = 0
    
    try:
        while True:
            # Check for 'q' or 'm' key press
            key = check_for_quit_or_menu()
            if key == 'q':
                cleanup_and_exit()
            elif key == 'm':
                # Return to main menu
                logger.info("\nReturning to main menu...")
                termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
                # Save any remaining CSV data before returning
                if csv_data_buffer:
                    save_csv_log()
                return
            
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
                
                # Get node stats
                online_nodes, total_nodes = get_node_stats()
                
                # Get target node info (signal strength and hops)
                snr, hops = get_target_node_info(TARGET_NODE_INT)
                
                # Format message using template
                message = format_message(temperature_f, humidity, online_nodes, total_nodes, snr, hops)
                
                # Try to reconnect if disconnected (check interval)
                if not meshtastic_connected:
                    if time.time() - last_reconnect_attempt >= USB_RECONNECT_INTERVAL:
                        logger.info(f"Meshtastic disconnected. Attempting to reconnect (every {USB_RECONNECT_INTERVAL}s)...")
                        check_and_reconnect_meshtastic()
                        last_reconnect_attempt = time.time()
                
                # Send message if connected
                if meshtastic_connected:
                    success = send_meshtastic_message(message)
                    
                    if success:
                        # Display confirmation on terminal
                        send_time = time.strftime("%H:%M:%S")
                        # Determine recipient(s)
                        if my_node_id and my_node_id in NODES.values():
                            my_node_name = next((name for name, node_id in NODES.items() if node_id == my_node_id), None)
                            if my_node_name:
                                recipients = [name for name, node_id in NODES.items() if node_id != my_node_id]
                                print(f"✓ Message sent to {', '.join(recipients)} at {send_time}")
                            else:
                                print(f"✓ Message sent to {SELECTED_NODE_NAME} at {send_time}")
                        else:
                            print(f"✓ Message sent to {SELECTED_NODE_NAME} at {send_time}")
                    
                    # Log node data after sending message
                    log_node_data()
                else:
                    logger.warning("Meshtastic not available. Skipping message send.")
                
                # Auto-save CSV log every AUTO_SAVE_INTERVAL seconds
                if time.time() - last_csv_save >= AUTO_SAVE_INTERVAL:
                    save_csv_log()
                    cleanup_old_logs()
                    
            else:
                # Display * when sensor fails, show last known reading
                if last_temperature_f is not None and last_humidity is not None:
                    logger.warning(f"* Temperature: {last_temperature_f:.1f}°F (last reading)")
                    logger.warning(f"* Humidity: {last_humidity:.1f}% (last reading)")
                else:
                    logger.warning("* No sensor data available yet")
                print("-" * 50)
            
            # Wait configured interval between readings
            time.sleep(UPDATE_INTERVAL)
    
    except KeyboardInterrupt:
        logger.info("\n\nExiting program...")
    
    except Exception as e:
        logger.error(f"Unexpected error in main loop: {e}", exc_info=True)
    
    except KeyboardInterrupt:
        logger.info("\nKeyboard interrupt received (Ctrl+C)")
        cleanup_and_exit()
    
    finally:
        # Save any remaining CSV data
        if csv_data_buffer:
            save_csv_log()
        
        # Restore terminal settings
        try:
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
        except:
            pass
        
        # Clean up if not already done
        if not shutdown_requested:
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
