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
import meshtastic.remote_hardware
from meshtastic import portnums_pb2
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
import threading
import base64
import json

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

# ACK/NAK tracking for message delivery confirmation
class AckTracker:
    """Track ACK/NAK responses for sent messages."""
    
    def __init__(self):
        self.pending = {}  # {message_id: {'node_name': name, 'ack_received': False, 'nak_received': False, 'snr': value}}
        self.lock = threading.Lock()
    
    def register_message(self, message_id, node_name, snr=None):
        """Register a sent message awaiting acknowledgment."""
        with self.lock:
            self.pending[message_id] = {
                'node_name': node_name,
                'ack_received': False,
                'nak_received': False,
                'impl_ack_received': False,
                'timestamp': time.time(),
                'snr': snr  # Store SNR from original message
            }
            logger.debug(f"Registered message {message_id} for node {node_name} with SNR {snr}")
    
    def on_ack_nak(self, packet):
        """Callback for ACK/NAK responses from Meshtastic."""
        try:
            # Handle both dict and protobuf packet formats
            if hasattr(packet, 'get'):
                # Dictionary format
                request_id = packet.get('decoded', {}).get('requestId')
                if not request_id:
                    request_id = packet.get('id')
                error_reason = packet.get('decoded', {}).get('routing', {}).get('errorReason', 'NONE')
                from_node = packet.get('from')
            else:
                # Protobuf format
                try:
                    request_id = packet.decoded.request_id if hasattr(packet.decoded, 'request_id') else packet.id
                    error_reason = packet.decoded.routing.error_reason if hasattr(packet.decoded, 'routing') else 'NONE'
                    from_node = packet.from_id if hasattr(packet, 'from_id') else None
                except AttributeError:
                    logger.debug(f"Could not parse packet format: {packet}")
                    return
            
            # Verbose logging for debugging
            logger.info(f"[ACK CALLBACK] Received packet - request_id: {request_id}, from_node: {from_node}, error: {error_reason}")
            
            if not request_id or request_id not in self.pending:
                if request_id:
                    logger.info(f"[ACK CALLBACK] Message {request_id} not in tracking (may have timed out or already processed)")
                    print(f"\n[ACK] Received response for message {request_id} (not currently tracked)")
                return
            
            with self.lock:
                msg_info = self.pending[request_id]
                node_name = msg_info['node_name']
                
                print(f"\n[ACK] Processing response for message {request_id} to {node_name}")
                
                # Check for NAK (error)
                if error_reason != 'NONE':
                    msg_info['nak_received'] = True
                    logger.warning(f"✗ NAK received from {node_name}: {error_reason}")
                    print(f"✗ NAK received from {node_name}: {error_reason}")
                else:
                    # Check if it's an implicit ACK or real ACK
                    local_num = meshtastic_interface.localNode.nodeNum if meshtastic_interface and hasattr(meshtastic_interface, 'localNode') else None
                    print(f"[ACK] Checking ACK type - from_node: {from_node}, local_num: {local_num}")
                    
                    if from_node == local_num:
                        msg_info['impl_ack_received'] = True
                        logger.info(f"⚠ Implicit ACK from {node_name} (packet queued, delivery not guaranteed)")
                        print(f"⚠ Implicit ACK from {node_name} (packet queued locally, delivery not guaranteed)")
                    else:
                        msg_info['ack_received'] = True
                        ack_time = time.strftime("%H:%M:%S")
                        logger.info(f"✓ ACK received from {node_name}")
                        print(f"✓ REAL ACK received from {node_name} at {ack_time}!")
                        
                        # Schedule ACK confirmation message (only if WANT_ACK is enabled)
                        if WANT_ACK:
                            snr = msg_info.get('snr')
                            threading.Timer(ACK_WAIT_TIME, self.send_ack_confirmation, args=(node_name, snr)).start()
                            logger.info(f"ACK confirmation scheduled for {node_name} in {ACK_WAIT_TIME} seconds")
                            print(f"[ACK] Confirmation message will be sent in {ACK_WAIT_TIME} seconds")
        
        except Exception as e:
            logger.error(f"Error in ACK/NAK callback: {e}")
    
    def get_status(self, message_id):
        """Get the status of a message: 'ack', 'nak', 'impl_ack', or 'pending'."""
        with self.lock:
            if message_id not in self.pending:
                return 'unknown'
            msg_info = self.pending[message_id]
            if msg_info['ack_received']:
                return 'ack'
            elif msg_info['nak_received']:
                return 'nak'
            elif msg_info['impl_ack_received']:
                return 'impl_ack'
            else:
                return 'pending'
    
    def cleanup_old(self, timeout=60):
        """Remove old pending messages that timed out."""
        with self.lock:
            current_time = time.time()
            expired = [msg_id for msg_id, info in self.pending.items() 
                      if current_time - info['timestamp'] > timeout]
            for msg_id in expired:
                node_name = self.pending[msg_id]['node_name']
                logger.warning(f"Message {msg_id} to {node_name} timed out without ACK")
                del self.pending[msg_id]
    
    def clear(self):
        """Clear all pending messages."""
        with self.lock:
            self.pending.clear()
    
    def send_ack_confirmation(self, node_name, snr):
        """Send ACK confirmation message to the node that acknowledged."""
        try:
            global meshtastic_interface, my_node_id
            
            if not meshtastic_interface or not WANT_ACK:
                return
            
            # Get the sender node name (our node)
            my_node_name = next((name for name, node_id in NODES.items() if node_id == my_node_id), "unknown")
            
            # Get the target node ID
            target_node_id = NODES.get(node_name)
            if not target_node_id:
                logger.warning(f"Cannot send ACK confirmation: node {node_name} not found in config")
                return
            
            # Format the ACK confirmation message
            date_time = time.strftime("%m/%d %H:%M:%S")
            snr_str = f"{snr:.1f}" if snr is not None else "--"
            
            ack_message = f"{my_node_name} ack\n{date_time}\nSNR: {snr_str}"
            
            logger.info(f"Sending ACK confirmation to {node_name}: {ack_message.replace(chr(10), ' | ')}")
            
            # Send the ACK confirmation message
            packet = meshtastic_interface.sendData(
                ack_message.encode('utf-8'),
                destinationId=target_node_id,
                portNum=portnums_pb2.PortNum.TEXT_MESSAGE_APP,
                wantAck=False,  # Don't request ACK for ACK confirmation
                hopLimit=HOP_LIMIT,
                channelIndex=CHANNEL_INDEX
            )
            
            if packet:
                logger.info(f"✓ ACK confirmation sent to {node_name}")
                print(f"\n✓ ACK confirmation sent to {node_name}")
            else:
                logger.warning(f"Failed to send ACK confirmation to {node_name}")
                
        except Exception as e:
            logger.error(f"Error sending ACK confirmation: {e}")

# Global ACK tracker
ack_tracker = AckTracker()

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
ACK_RETRY_TIMEOUT = 60
ACK_WAIT_TIME = 30  # Seconds to wait for ACK confirmation message
WANT_ACK = False
MESH_SEND_MODE = 'mesh'  # 'mesh' or 'direct'
HOP_LIMIT = 3  # Will be set based on MESH_SEND_MODE
CHANNEL_INDEX = 0  # Channel to send messages on (0 = primary/private, others = secondary/public)
PKI_ENCRYPTED = False  # Use public key encryption
PUBLIC_KEYS = {}  # {node_name: base64_encoded_public_key}
LOG_FILE = 'meshtastic_log.csv'
AUTO_SAVE_INTERVAL = 300
RETENTION_DAYS = 7
MESSAGE_TEMPLATE = 'template1'
MESSAGE_TEMPLATES = {}
LAST_ACK_STATUS = None  # Track last message ACK status: 'A' for ack, 'U' for unack, None for no previous message
SNR_STATS_FILE = 'snr_stats.json'  # File to track SNR statistics per node
SNR_STATS = {}  # {node_name: {'min': float, 'max': float, 'avg': float, 'count': int, 'recent': [float]}}

def load_config():
    """Load configuration from config.ini file."""
    global NODES, SELECTED_NODE_NAME, TARGET_NODE_INT, UPDATE_INTERVAL
    global AUTO_BOOT_TIMEOUT, USB_RECONNECT_INTERVAL, LOG_FILE, AUTO_SAVE_INTERVAL, RETENTION_DAYS
    global MESSAGE_TEMPLATE, MESSAGE_TEMPLATES, ACK_RETRY_TIMEOUT, ACK_WAIT_TIME, WANT_ACK, MESH_SEND_MODE, HOP_LIMIT
    global PKI_ENCRYPTED, PUBLIC_KEYS, CHANNEL_INDEX
    
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
        ACK_RETRY_TIMEOUT = config.getint('settings', 'ack_retry_timeout', fallback=60)
        ACK_WAIT_TIME = config.getint('settings', 'ack_wait_time', fallback=30)
        CHANNEL_INDEX = config.getint('settings', 'channel_index', fallback=0)
        want_ack_str = config.get('settings', 'want_ack', fallback='off').lower()
        WANT_ACK = want_ack_str in ['on', 'true', 'yes', '1']
        
        # Load mesh send mode setting
        MESH_SEND_MODE = config.get('settings', 'mesh_send_mode', fallback='mesh').lower()
        if MESH_SEND_MODE not in ['mesh', 'direct']:
            logger.warning(f"Invalid mesh_send_mode '{MESH_SEND_MODE}', defaulting to 'mesh'")
            MESH_SEND_MODE = 'mesh'
        
        # Set hop limit based on mode
        if MESH_SEND_MODE == 'direct':
            HOP_LIMIT = 0  # Direct to neighbors only, no mesh routing
        else:  # 'mesh'
            HOP_LIMIT = 3  # Allow mesh routing through up to 3 hops
        
        # Load PKI encryption setting
        pki_encrypted_str = config.get('settings', 'pki_encrypted', fallback='off').lower()
        PKI_ENCRYPTED = pki_encrypted_str in ['on', 'true', 'yes', '1']
    else:
        SELECTED_NODE_NAME = 'yang'
        MESSAGE_TEMPLATE = 'template1'
        ACK_RETRY_TIMEOUT = 60
        ACK_WAIT_TIME = 30
        WANT_ACK = False
        MESH_SEND_MODE = 'mesh'
        HOP_LIMIT = 3
        PKI_ENCRYPTED = False
        CHANNEL_INDEX = 0
    
    # Load public keys for PKI encryption
    if config.has_section('public_keys'):
        PUBLIC_KEYS = {}
        for name, key_b64 in config.items('public_keys'):
            try:
                # Decode base64 public key to bytes (Meshtastic API expects bytes)
                PUBLIC_KEYS[name] = base64.b64decode(key_b64)
                logger.debug(f"Loaded public key for {name}")
            except Exception as e:
                logger.warning(f"Failed to decode public key for {name}: {e}")
    else:
        PUBLIC_KEYS = {}
    
    if PKI_ENCRYPTED and not PUBLIC_KEYS:
        logger.warning("PKI encryption enabled but no public keys configured!")
    
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
    logger.info(f"Mesh send mode: {MESH_SEND_MODE} (hop_limit={HOP_LIMIT})")
    if PKI_ENCRYPTED:
        logger.info(f"PKI encryption: ENABLED (public keys loaded for {len(PUBLIC_KEYS)} nodes)")
    else:
        logger.info(f"PKI encryption: DISABLED (using channel encryption)")

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
    receiver_info = f"{SELECTED_NODE_NAME} (ID: {TARGET_NODE_INT})"
    
    if my_node_id:
        sender_name = next((name for name, node_id in NODES.items() if node_id == my_node_id), None)
        if sender_name:
            sender_info = f"{sender_name} (ID: {my_node_id})"
            # If sender is in config, show all other nodes as receivers
            other_nodes = [f"{name} (ID: {node_id})" for name, node_id in NODES.items() if node_id != my_node_id]
            if other_nodes:
                receiver_info = ", ".join(other_nodes)
        else:
            sender_info = f"Unknown (ID: {my_node_id})"
    
    print("\n" + "="*60)
    print("MESHTASTIC WEATHER STATION - MAIN MENU")
    print("="*60)
    print(f"\nConnected Node (Sender): {sender_info}")
    print(f"Target Node (Receiver):  {receiver_info}")
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
    receiver_info = f"{SELECTED_NODE_NAME} (ID: {TARGET_NODE_INT})"
    
    if my_node_id:
        sender_name = next((name for name, node_id in NODES.items() if node_id == my_node_id), None)
        if sender_name:
            sender_info = f"{sender_name} (ID: {my_node_id})"
            # If sender is in config, show all other nodes as receivers
            other_nodes = [f"{name} (ID: {node_id})" for name, node_id in NODES.items() if node_id != my_node_id]
            if other_nodes:
                receiver_info = ", ".join(other_nodes)
        else:
            sender_info = f"Unknown (ID: {my_node_id})"
    
    print("\n" + "="*60)
    print("MESHTASTIC WEATHER STATION - MAIN MENU")
    print("="*60)
    print(f"\nConnected Node (Sender): {sender_info}")
    print(f"Target Node (Receiver):  {receiver_info}")
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
        
        # Display current mesh routing mode
        mesh_mode_display = "mesh (hop_limit=3)" if MESH_SEND_MODE == 'mesh' else "direct (hop_limit=0)"
        print(f"5. Change Mesh Routing Mode (current: {mesh_mode_display})")
        
        # Display current ACK setting
        ack_display = "ON" if WANT_ACK else "OFF"
        print(f"6. Toggle Message ACK (current: {ack_display})")
        
        # Display current PKI setting
        pki_display = "ON" if PKI_ENCRYPTED else "OFF"
        print(f"7. Toggle PKI Encryption (current: {pki_display})")
        
        print("8. Scan/Update Public Keys")
        print(f"9. Change ACK Wait Time (current: {ACK_WAIT_TIME}s)")
        print("10. Back to Main Menu")
        print("\n" + "="*60)
        
        try:
            choice = input("\nSelect option (1-10): ").strip()
            
            if choice == '1':
                show_node_selection_menu()
            elif choice == '2':
                change_update_interval()
            elif choice == '3':
                change_reconnect_interval()
            elif choice == '4':
                change_retention_days()
            elif choice == '5':
                change_mesh_routing_mode()
            elif choice == '6':
                toggle_want_ack()
            elif choice == '7':
                toggle_pki_encryption()
            elif choice == '8':
                scan_and_update_public_keys()
            elif choice == '9':
                change_ack_wait_time()
            elif choice == '10':
                break
            else:
                print("\nInvalid option. Please select 1-10.")
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

def change_mesh_routing_mode():
    """Change the mesh routing mode setting."""
    global MESH_SEND_MODE, HOP_LIMIT
    
    print("\n" + "="*60)
    print("MESH ROUTING MODE")
    print("="*60)
    print("\nCurrent mode: " + ("mesh (hop_limit=3)" if MESH_SEND_MODE == 'mesh' else "direct (hop_limit=0)"))
    print("\nAvailable modes:")
    print("  1. mesh   - Route through mesh network (hop_limit=3)")
    print("             Use when nodes are far apart")
    print("  2. direct - Send only to direct neighbors (hop_limit=0)")
    print("             Use when nodes are co-located")
    print("\n" + "="*60)
    
    try:
        choice = input("\nSelect mode (1=mesh, 2=direct) or press Enter to cancel: ").strip()
        
        if choice == '1':
            MESH_SEND_MODE = 'mesh'
            HOP_LIMIT = 3
        elif choice == '2':
            MESH_SEND_MODE = 'direct'
            HOP_LIMIT = 0
        else:
            print("Mesh routing mode unchanged.")
            return
        
        # Save to config
        if not config.has_section('settings'):
            config.add_section('settings')
        config.set('settings', 'mesh_send_mode', MESH_SEND_MODE)
        save_config()
        
        mode_desc = "mesh (hop_limit=3)" if MESH_SEND_MODE == 'mesh' else "direct (hop_limit=0)"
        print(f"✓ Mesh routing mode changed to: {mode_desc}")
        
    except (KeyboardInterrupt, EOFError):
        print("\nMesh routing mode unchanged.")

def toggle_want_ack():
    """Toggle the want_ack setting."""
    global WANT_ACK
    
    current_state = "ON" if WANT_ACK else "OFF"
    new_state = "OFF" if WANT_ACK else "ON"
    
    print(f"\nCurrent ACK setting: {current_state}")
    print(f"Change to: {new_state}?")
    print("\nACK ON:  Messages request delivery confirmation (slower, more reliable)")
    print("ACK OFF: Messages sent without confirmation (faster, less reliable)")
    
    try:
        confirm = input("\nConfirm change? (y/n): ").strip().lower()
        
        if confirm == 'y':
            WANT_ACK = not WANT_ACK
            
            # Save to config
            if not config.has_section('settings'):
                config.add_section('settings')
            config.set('settings', 'want_ack', 'on' if WANT_ACK else 'off')
            save_config()
            
            new_state = "ON" if WANT_ACK else "OFF"
            print(f"✓ Message ACK changed to: {new_state}")
        else:
            print("ACK setting unchanged.")
            
    except (KeyboardInterrupt, EOFError):
        print("\nACK setting unchanged.")

def change_ack_wait_time():
    """Change the ACK wait time setting."""
    global ACK_WAIT_TIME
    
    print("\n" + "="*60)
    print("ACK WAIT TIME CONFIGURATION")
    print("="*60)
    print(f"\nCurrent ACK wait time: {ACK_WAIT_TIME} seconds")
    print("\nThis is the delay before sending an ACK confirmation message.")
    print("Recommended: 30-60 seconds for mesh networks (slow propagation)")
    print("            10-20 seconds for direct communication")
    print("\n" + "="*60)
    
    try:
        new_wait = input(f"\nEnter new ACK wait time in seconds (current: {ACK_WAIT_TIME}): ").strip()
        if new_wait:
            wait_time = int(new_wait)
            if wait_time < 5:
                print("⚠ Warning: Wait time less than 5 seconds may be too short for mesh.")
                confirm = input("Continue anyway? (y/n): ").strip().lower()
                if confirm != 'y':
                    print("ACK wait time unchanged.")
                    return
            
            ACK_WAIT_TIME = wait_time
            if not config.has_section('settings'):
                config.add_section('settings')
            config.set('settings', 'ack_wait_time', str(ACK_WAIT_TIME))
            save_config()
            print(f"✓ ACK wait time changed to {ACK_WAIT_TIME} seconds")
    except ValueError:
        print("Invalid input. ACK wait time unchanged.")
    except (KeyboardInterrupt, EOFError):
        print("\nACK wait time unchanged.")

def toggle_pki_encryption():
    """Toggle the PKI encryption setting."""
    global PKI_ENCRYPTED
    
    current_state = "ON" if PKI_ENCRYPTED else "OFF"
    new_state = "OFF" if PKI_ENCRYPTED else "ON"
    
    print(f"\nCurrent PKI encryption: {current_state}")
    print(f"Change to: {new_state}?")
    print("\nPKI ON:  Use public key encryption (requires public keys in config)")
    print("PKI OFF: Use channel encryption (default)")
    
    if not PKI_ENCRYPTED and len(PUBLIC_KEYS) == 0:
        print("\n⚠ WARNING: No public keys configured in config.ini!")
        print("  Add keys to [public_keys] section before enabling PKI.")
    
    try:
        confirm = input("\nConfirm change? (y/n): ").strip().lower()
        
        if confirm == 'y':
            PKI_ENCRYPTED = not PKI_ENCRYPTED
            
            # Save to config
            if not config.has_section('settings'):
                config.add_section('settings')
            config.set('settings', 'pki_encrypted', 'on' if PKI_ENCRYPTED else 'off')
            save_config()
            
            new_state = "ON" if PKI_ENCRYPTED else "OFF"
            print(f"✓ PKI encryption changed to: {new_state}")
            
            if PKI_ENCRYPTED and len(PUBLIC_KEYS) > 0:
                print(f"  Using public keys for {len(PUBLIC_KEYS)} node(s): {', '.join(PUBLIC_KEYS.keys())}")
            elif PKI_ENCRYPTED:
                print("  ⚠ No public keys loaded - will fall back to channel encryption")
        else:
            print("PKI encryption unchanged.")
            
    except (KeyboardInterrupt, EOFError):
        print("\nPKI encryption unchanged.")

def scan_and_update_public_keys():
    """Scan for public keys from configured nodes and update config.ini."""
    global PUBLIC_KEYS, meshtastic_interface, meshtastic_connected
    
    print("\n" + "="*60)
    print("SCAN AND UPDATE PUBLIC KEYS")
    print("="*60)
    
    # Check if Meshtastic is connected, if not try to connect
    if not meshtastic_interface or not meshtastic_connected:
        print("\nMeshtastic not connected. Attempting to connect...")
        if not init_meshtastic():
            print("\n✗ Error: Failed to connect to Meshtastic!")
            print("  Please ensure Meshtastic device is connected via USB.")
            input("\nPress Enter to continue...")
            return
        print("✓ Connected to Meshtastic")
        print("\n⏳ Waiting 5 seconds for node database to populate...")
        time.sleep(5)
    
    # Check how many nodes are in the database
    nodes_in_db = 0
    if hasattr(meshtastic_interface, 'nodes'):
        nodes_in_db = len(meshtastic_interface.nodes)
    
    print(f"\nℹ Nodes in mesh database: {nodes_in_db}")
    
    if nodes_in_db == 0:
        print("\n⚠ WARNING: No nodes found in mesh database!")
        print("  The device needs to hear from nodes on the mesh first.")
        print("\nSuggestions:")
        print("  1. Start the weather station and let it run for a few minutes")
        print("  2. Send a message to populate the node database")
        print("  3. Wait for nodes to transmit on the mesh")
        print("\nYou can still try scanning, but it will likely fail.")
        
        try:
            cont = input("\nContinue anyway? (y/n): ").strip().lower()
            if cont != 'y':
                print("Cancelled.")
                input("\nPress Enter to continue...")
                return
        except (KeyboardInterrupt, EOFError):
            print("\nCancelled.")
            input("\nPress Enter to continue...")
            return
    
    print("\nThis will scan all configured nodes for their public keys.")
    print("You can choose to:")
    print("  1. Add keys for nodes that don't have keys yet")
    print("  2. Update all keys (including existing ones)")
    print("  3. Cancel")
    
    try:
        choice = input("\nSelect option (1-3): ").strip()
        
        if choice == '3':
            print("Cancelled.")
            return
        
        update_existing = (choice == '2')
        
        print("\nScanning nodes...")
        print("-" * 60)
        
        # Track results
        keys_added = []
        keys_updated = []
        keys_failed = []
        keys_skipped = []
        
        # Debug: Show what's in the nodes database
        if hasattr(meshtastic_interface, 'nodes'):
            print(f"\nDebug - Node IDs in database (first 5):")
            for idx, node_key in enumerate(list(meshtastic_interface.nodes.keys())[:5]):
                print(f"  {idx+1}. {node_key} (type: {type(node_key).__name__})")
            print()
        
        for node_name, node_id in NODES.items():
            # Check if we should skip this node
            if node_name in PUBLIC_KEYS and not update_existing:
                print(f"⊘ {node_name}: Skipped (already has key, use update to refresh)")
                keys_skipped.append(node_name)
                continue
            
            print(f"⌛ {node_name}: Requesting public key from node {node_id}...", end='', flush=True)
            
            try:
                # Get node info from Meshtastic
                # Meshtastic stores node IDs as hex strings with ! prefix
                # Convert decimal to hex format: 2658499212 -> !9e757a8c
                node_id_hex = f"!{node_id:08x}"
                
                node = None
                if hasattr(meshtastic_interface, 'nodes'):
                    # Try hex format (most common)
                    if node_id_hex in meshtastic_interface.nodes:
                        node = meshtastic_interface.nodes[node_id_hex]
                    # Fallback: try integer
                    elif node_id in meshtastic_interface.nodes:
                        node = meshtastic_interface.nodes[node_id]
                    # Fallback: try string decimal
                    elif str(node_id) in meshtastic_interface.nodes:
                        node = meshtastic_interface.nodes[str(node_id)]
                
                if not node:
                    print(f" ✗ Not found in mesh (searched for {node_id_hex})")
                    keys_failed.append(node_name)
                    continue
                
                # Check if node has a public key
                if hasattr(node, 'user') and hasattr(node.user, 'publicKey') and node.user.publicKey:
                    public_key_base64 = base64.b64encode(node.user.publicKey).decode('utf-8')
                    
                    # Update in memory
                    was_existing = node_name in PUBLIC_KEYS
                    PUBLIC_KEYS[node_name] = public_key_base64
                    
                    # Update config file
                    if not config.has_section('public_keys'):
                        config.add_section('public_keys')
                    config.set('public_keys', node_name, public_key_base64)
                    
                    if was_existing:
                        print(f" ✓ Updated")
                        keys_updated.append(node_name)
                    else:
                        print(f" ✓ Added")
                        keys_added.append(node_name)
                else:
                    print(f" ✗ No public key available (PKI not enabled on node)")
                    keys_failed.append(node_name)
                    
            except Exception as e:
                print(f" ✗ Error: {e}")
                keys_failed.append(node_name)
        
        # Save config file
        if keys_added or keys_updated:
            save_config()
            print("\n" + "-" * 60)
            print("✓ Configuration saved to config.ini")
        
        # Display summary
        print("\n" + "="*60)
        print("SUMMARY")
        print("="*60)
        
        if keys_added:
            print(f"\n✓ Keys added ({len(keys_added)}):")
            for name in keys_added:
                print(f"  • {name}")
        
        if keys_updated:
            print(f"\n✓ Keys updated ({len(keys_updated)}):")
            for name in keys_updated:
                print(f"  • {name}")
        
        if keys_skipped:
            print(f"\n⊘ Keys skipped ({len(keys_skipped)}):")
            for name in keys_skipped:
                print(f"  • {name}")
        
        if keys_failed:
            print(f"\n✗ Failed to get keys ({len(keys_failed)}):")
            for name in keys_failed:
                node_id = NODES.get(name, 'unknown')
                print(f"  • {name} (ID: {node_id})")
            
            print("\nPossible reasons:")
            print("  • Nodes don't have PKI encryption enabled (most common)")
            print("  • Nodes are offline or out of range")
            print("  • Nodes not heard on mesh yet (need to receive packets)")
            
            print("\nTo enable PKI on nodes:")
            print("  Run on each node: meshtastic --set security.public_key true")
            print("  Or via app: Settings → Security → Enable 'Public Key'")
            
            print("\nNote: PKI encryption requires firmware 2.0+ and may not be")
            print("      available on all devices. Check Meshtastic documentation.")
        
        total_keys = len(PUBLIC_KEYS)
        print(f"\nTotal public keys in config: {total_keys}")
        
        if total_keys > 0:
            print("\nTo use PKI encryption:")
            print("  1. Go to Options menu")
            print("  2. Select 'Toggle PKI Encryption'")
            print("  3. Enable PKI encryption")
        
        print("="*60)
        
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled.")
    
    input("\nPress Enter to continue...")

def show_menu():
    """Deprecated - kept for compatibility. Use show_node_selection_menu instead."""
    show_node_selection_menu()

def show_snr_stats_report():
    """Display SNR statistics for all nodes."""
    if not SNR_STATS:
        print("\n" + "="*70)
        print("SNR STATISTICS")
        print("="*70)
        print("\nNo SNR statistics available yet.")
        print("Statistics will be collected as messages are received.")
        print("\n" + "="*70)
        input("\nPress Enter to continue...")
        return
    
    print("\n" + "="*80)
    print("SNR STATISTICS (Signal-to-Noise Ratio)")
    print("="*80)
    print("\nMin SNR = Cutoff (weakest signal seen)")
    print("Max SNR = Optimal (strongest signal seen)")
    print("\n" + "-"*80)
    print(f"{'Node Name':<20} {'Min (Cutoff)':<15} {'Max (Optimal)':<15} {'Average':<10} {'Count':<10}")
    print("-"*80)
    
    # Sort nodes alphabetically
    for node_name in sorted(SNR_STATS.keys()):
        stats = SNR_STATS[node_name]
        min_snr = stats.get('min', 'N/A')
        max_snr = stats.get('max', 'N/A')
        avg_snr = stats.get('avg', 'N/A')
        count = stats.get('count', 0)
        
        # Format SNR values
        min_str = f"{min_snr:.1f} dB" if isinstance(min_snr, (int, float)) else min_snr
        max_str = f"{max_snr:.1f} dB" if isinstance(max_snr, (int, float)) else max_snr
        avg_str = f"{avg_snr:.1f} dB" if isinstance(avg_snr, (int, float)) else avg_snr
        
        print(f"{node_name:<20} {min_str:<15} {max_str:<15} {avg_str:<10} {count:<10}")
        
        # Show recent trend (last 10 values)
        recent = stats.get('recent', [])
        if recent:
            recent_10 = recent[-10:]
            recent_str = ', '.join([f"{val:.1f}" for val in recent_10])
            print(f"  Recent trend: {recent_str}")
    
    print("="*80)
    print(f"\nTotal nodes tracked: {len(SNR_STATS)}")
    print(f"Statistics file: {SNR_STATS_FILE}")
    print("\n" + "="*80)
    input("\nPress Enter to continue...")

def show_reports_menu():
    """Display reports menu."""
    while True:
        print("\n" + "="*60)
        print("REPORTS MENU")
        print("="*60)
        print("\n1. List of Nodes Seen")
        print("2. SNR Statistics")
        print("3. Back to Main Menu")
        print("\n" + "="*60)
        
        try:
            choice = input("\nSelect option (1-3): ").strip()
            
            if choice == '1':
                show_nodes_seen_report()
            elif choice == '2':
                show_snr_stats_report()
            elif choice == '3':
                break
            else:
                print("\nInvalid option. Please select 1-3.")
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

def load_snr_stats():
    """Load SNR statistics from JSON file."""
    global SNR_STATS
    
    if not os.path.exists(SNR_STATS_FILE):
        SNR_STATS = {}
        return
    
    try:
        with open(SNR_STATS_FILE, 'r') as f:
            SNR_STATS = json.load(f)
        logger.debug(f"Loaded SNR stats for {len(SNR_STATS)} nodes")
    except Exception as e:
        logger.error(f"Error loading SNR stats: {e}")
        SNR_STATS = {}

def save_snr_stats():
    """Save SNR statistics to JSON file."""
    global SNR_STATS
    
    try:
        with open(SNR_STATS_FILE, 'w') as f:
            json.dump(SNR_STATS, f, indent=2)
        logger.debug(f"Saved SNR stats for {len(SNR_STATS)} nodes")
    except Exception as e:
        logger.error(f"Error saving SNR stats: {e}")

def update_snr_stats(node_name, snr):
    """
    Update SNR statistics for a node.
    Tracks min, max, average, and recent SNR values.
    
    Args:
        node_name: Name of the node
        snr: Current SNR value
    """
    global SNR_STATS
    
    if snr is None:
        return
    
    if node_name not in SNR_STATS:
        SNR_STATS[node_name] = {
            'min': snr,
            'max': snr,
            'avg': snr,
            'count': 1,
            'recent': [snr]
        }
    else:
        stats = SNR_STATS[node_name]
        
        # Update min/max
        stats['min'] = min(stats['min'], snr)
        stats['max'] = max(stats['max'], snr)
        
        # Update running average
        total = stats['avg'] * stats['count']
        stats['count'] += 1
        stats['avg'] = (total + snr) / stats['count']
        
        # Keep last 100 recent values for trend analysis
        stats['recent'].append(snr)
        if len(stats['recent']) > 100:
            stats['recent'].pop(0)
    
    # Save periodically (every 10 updates)
    if SNR_STATS[node_name]['count'] % 10 == 0:
        save_snr_stats()

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
            
            # Update SNR statistics if we have a node name
            if snr is not None:
                # Find node name from ID
                node_name = None
                for name, nid in NODES.items():
                    if nid == target_node_id:
                        node_name = name
                        break
                
                if node_name:
                    update_snr_stats(node_name, snr)
            
            return snr, hops
        else:
            logger.debug(f"Target node {node_hex} not found in nodes list")
            return None, None
    except Exception as e:
        logger.warning(f"Error getting target node info: {e}")
        return None, None

def format_message(temperature_f, humidity, online_nodes=None, total_nodes=None, snr=None, hops=None):
    """Format message using the configured template."""
    global MESSAGE_TEMPLATE, MESSAGE_TEMPLATES, LAST_ACK_STATUS
    
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
    
    # Add ACK status indicator
    ack_status = LAST_ACK_STATUS if LAST_ACK_STATUS else ""
    
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
        hops=hops_val,
        ack=ack_status
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
        
        # Register ACK/NAK callback
        if WANT_ACK:
            meshtastic_interface.acknowledgmentCallback = ack_tracker.on_ack_nak
            logger.info("ACK/NAK callback registered (want_ack=on)")
            print("[ACK] ACK tracking enabled - callback registered")
            print(f"[ACK] Callback function: {ack_tracker.on_ack_nak}")
        else:
            logger.info("ACK/NAK callback not registered (want_ack=off)")
            print("[ACK] ACK tracking disabled (want_ack=off)")
        
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

def send_meshtastic_message(message, snr=None):
    """
    Send a private message to configured nodes with delivery confirmation.
    If connected device matches a node in config, sends to all other nodes.
    Otherwise, sends to the selected target node.
    Returns dict: {'sent': count, 'acked': [], 'nacked': [], 'pending': []}
    
    Args:
        message: The message text to send
        snr: Signal-to-noise ratio of the target node (for ACK confirmation)
    """
    global meshtastic_interface, meshtastic_connected, my_node_id
    
    if not meshtastic_interface:
        logger.warning("Meshtastic interface not available")
        return {'sent': 0, 'acked': [], 'nacked': [], 'pending': [], 'message_ids': {}}
    
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
        
        # Send messages to all target nodes with ACK request
        success_count = 0
        message_ids = {}  # {message_id: node_name}
        
        for name, node_id in target_nodes:
            try:
                mode_desc = "direct (no mesh)" if MESH_SEND_MODE == 'direct' else "mesh routing"
                logger.info(f"Attempting to send message to {name} (ID: {node_id}) via {mode_desc}...")
                
                # Get public key if PKI encryption is enabled
                public_key = None
                use_pki = False
                if PKI_ENCRYPTED:
                    public_key = PUBLIC_KEYS.get(name)
                    if public_key:
                        logger.info(f"Using PKI encryption for {name} with public key")
                        use_pki = True
                    else:
                        logger.warning(f"PKI encryption enabled but no public key found for {name}, using channel encryption")
                
                # Always use sendData to support hopLimit parameter
                # sendText doesn't support hopLimit in this version
                if WANT_ACK:
                    packet = meshtastic_interface.sendData(
                        message.encode('utf-8'),
                        destinationId=node_id,
                        portNum=portnums_pb2.PortNum.TEXT_MESSAGE_APP,
                        wantAck=True,
                        onResponse=ack_tracker.on_ack_nak,
                        hopLimit=HOP_LIMIT,
                        channelIndex=CHANNEL_INDEX,
                        pkiEncrypted=use_pki,
                        publicKey=public_key if use_pki else None
                    )
                else:
                    packet = meshtastic_interface.sendData(
                        message.encode('utf-8'),
                        destinationId=node_id,
                        portNum=portnums_pb2.PortNum.TEXT_MESSAGE_APP,
                        wantAck=False,
                        hopLimit=HOP_LIMIT,
                        channelIndex=CHANNEL_INDEX,
                        pkiEncrypted=use_pki,
                        publicKey=public_key if use_pki else None
                    )
                
                # Register this message for ACK tracking only if ACK requested
                # packet is a MeshPacket protobuf object, not a dict
                if packet and WANT_ACK:
                    try:
                        message_id = packet.id
                        ack_tracker.register_message(message_id, name, snr)
                        message_ids[message_id] = name
                        logger.info(f"✓ Message queued for {name} (ID: {node_id}, msg_id: {message_id})")
                        print(f"\n[ACK] Message {message_id} sent to {name}, waiting for ACK...")
                        print(f"[ACK] Callback registered: {ack_tracker.on_ack_nak}")
                        success_count += 1
                    except AttributeError:
                        # Fallback if packet doesn't have id attribute
                        logger.info(f"✓ Message queued for {name} (ID: {node_id})")
                        print(f"\n[ACK] Message sent to {name}, but couldn't get message ID for tracking")
                        success_count += 1
                elif packet:
                    logger.info(f"✓ Message sent to {name} (ID: {node_id}, no ACK requested)")
                    print(f"\n[INFO] Message sent to {name} (ACK not requested)")
                    success_count += 1
                else:
                    logger.info(f"✓ Message queued for {name} (ID: {node_id})")
                    success_count += 1
                    
            except Exception as e:
                logger.error(f"Failed to send to {name} (ID: {node_id}): {e}")
        
        logger.info(f"Message content: {message}")
        logger.info(f"Successfully queued to {success_count}/{len(target_nodes)} nodes")
        
        # Wait briefly for ACKs (non-blocking approach)
        if message_ids:
            time.sleep(0.5)  # Brief wait for immediate ACKs
            
            # Check status of each message
            acked = []
            nacked = []
            pending = []
            
            for msg_id, node_name in message_ids.items():
                status = ack_tracker.get_status(msg_id)
                if status == 'ack':
                    acked.append(node_name)
                elif status == 'nak':
                    nacked.append(node_name)
                else:
                    pending.append(node_name)
            
            return {
                'sent': success_count,
                'acked': acked,
                'nacked': nacked,
                'pending': pending,
                'message_ids': message_ids  # Include message IDs for tracking
            }
        
        return {'sent': success_count, 'acked': [], 'nacked': [], 'pending': [], 'message_ids': {}}
        
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
        return {'sent': 0, 'acked': [], 'nacked': [], 'pending': []}

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
    global meshtastic_connected, last_csv_save, LAST_ACK_STATUS
    
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
    
    # Load SNR statistics
    load_snr_stats()
    
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
    last_minute_sent = -1  # Track last minute we sent a message
    pending_retry_time = None  # Track when to retry pending messages
    pending_message = None  # Store message for retry
    pending_recipients = []  # Track who we're waiting for
    first_message_sent = False  # Track if we've sent the initial message
    
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
            current_minute = int(time.localtime(current_time).tm_min)
            current_second = int(time.localtime(current_time).tm_sec)
            
            # Check if we need to retry a pending message (only if ACK is enabled)
            if WANT_ACK and pending_retry_time and current_time >= pending_retry_time:
                logger.info(f"Retry timeout reached for pending message to: {', '.join(pending_recipients)}")
                logger.info("Retrying message send...")
                
                # Clear the retry state
                pending_retry_time = None
                retry_message = pending_message
                pending_message = None
                pending_recipients = []
                
                # Resend the message
                if meshtastic_connected and retry_message:
                    send_time = time.strftime("%H:%M:%S")
                    result = send_meshtastic_message(retry_message)
                    
                    if result['sent'] > 0:
                        # Determine recipient(s)
                        if my_node_id and my_node_id in NODES.values():
                            my_node_name = next((name for name, node_id in NODES.items() if node_id == my_node_id), None)
                            if my_node_name:
                                recipients = [name for name, node_id in NODES.items() if node_id != my_node_id]
                                recipient_text = ', '.join(recipients)
                            else:
                                recipient_text = SELECTED_NODE_NAME
                        else:
                            recipient_text = SELECTED_NODE_NAME
                        
                        print("\n" + "=" * 60)
                        print(f"🔄 RETRY To: {recipient_text}")
                        print(f"Sent: {send_time}")
                        
                        # Set up new retry if still pending
                        current_msg_ids = result.get('message_ids', {})
                        
                        def check_ack_received():
                            for msg_id in current_msg_ids.keys():
                                status = ack_tracker.get_status(msg_id)
                                if status == 'ack':
                                    return True
                            return False
                        
                        # Wait 5 seconds for ACK
                        time.sleep(5)
                        ack_time = time.strftime("%H:%M:%S")
                        
                        # Check final status
                        acked = []
                        nacked = []
                        pending = []
                        
                        for msg_id, node_name in current_msg_ids.items():
                            status = ack_tracker.get_status(msg_id)
                            if status == 'ack':
                                acked.append(node_name)
                            elif status == 'nak':
                                nacked.append(node_name)
                            elif status == 'pending':
                                pending.append(node_name)
                        
                        if acked:
                            for node_name in acked:
                                node_id = NODES.get(node_name)
                                if node_id:
                                    snr, _ = get_target_node_info(node_id)
                                    snr_display = f"{snr:.1f}" if snr is not None else "--"
                                else:
                                    snr_display = "--"
                                
                                print(f"Ack : {ack_time}")
                                print(f"SNR : {snr_display}")
                                print(f"✓ {node_name}")
                        
                        if nacked:
                            print(f"✗ NAK from: {', '.join(nacked)}")
                        
                        if pending:
                            print(f"⏳ Still pending from: {', '.join(pending)}")
                            # Set another retry timer
                            pending_retry_time = time.time() + ACK_RETRY_TIMEOUT
                            pending_message = retry_message
                            pending_recipients = pending
                            logger.info(f"Will retry again in {ACK_RETRY_TIMEOUT} seconds at {time.strftime('%H:%M:%S', time.localtime(pending_retry_time))}")
                        else:
                            # All resolved
                            pending_retry_time = None
                            pending_message = None
                            pending_recipients = []
                        
                        print("=" * 60)
            
            if temperature_c is not None and humidity is not None:
                # Convert to Fahrenheit
                temperature_f = temperature_c * (9 / 5) + 32
                
                # Store last valid readings
                last_temperature_f = temperature_f
                last_humidity = humidity
                
                # Only display readings when not just counting down (debug level logging instead)
                logger.debug(f"Temperature: {temperature_f:.1f}°F")
                logger.debug(f"Humidity: {humidity:.1f}%")
                
                # Get node stats
                online_nodes, total_nodes = get_node_stats()
                
                # Get target node info (signal strength and hops)
                # Determine the actual target we're sending to
                if my_node_id and my_node_id in NODES.values():
                    # We're one of the configured nodes, get info for another node
                    # Find first other node for signal info
                    target_for_signal = None
                    for name, node_id in NODES.items():
                        if node_id != my_node_id:
                            target_for_signal = node_id
                            break
                    snr, hops = get_target_node_info(target_for_signal) if target_for_signal else (None, None)
                else:
                    # Use configured target node
                    snr, hops = get_target_node_info(TARGET_NODE_INT)
                
                # Format message using template
                message = format_message(temperature_f, humidity, online_nodes, total_nodes, snr, hops)
                
                # Try to reconnect if disconnected (check interval)
                if not meshtastic_connected:
                    if time.time() - last_reconnect_attempt >= USB_RECONNECT_INTERVAL:
                        logger.info(f"Meshtastic disconnected. Attempting to reconnect (every {USB_RECONNECT_INTERVAL}s)...")
                        check_and_reconnect_meshtastic()
                        last_reconnect_attempt = time.time()
                
                # Send message if connected AND (it's the first message OR it's a whole minute AND we haven't sent this minute yet)
                should_send_first = meshtastic_connected and not first_message_sent
                should_send_regular = meshtastic_connected and current_second == 0 and current_minute != last_minute_sent
                
                if should_send_first or should_send_regular:
                    if should_send_first:
                        first_message_sent = True
                        logger.info("Sending initial message immediately...")
                    
                    last_minute_sent = current_minute
                    
                    # Record send time
                    send_time = time.strftime("%H:%M:%S")
                    
                    result = send_meshtastic_message(message, snr)
                    
                    if result['sent'] > 0:
                        # Determine recipient(s)
                        if my_node_id and my_node_id in NODES.values():
                            my_node_name = next((name for name, node_id in NODES.items() if node_id == my_node_id), None)
                            if my_node_name:
                                recipients = [name for name, node_id in NODES.items() if node_id != my_node_id]
                                recipient_text = ', '.join(recipients)
                            else:
                                recipient_text = SELECTED_NODE_NAME
                        else:
                            recipient_text = SELECTED_NODE_NAME
                        
                        # Display timing and status information
                        print("\n" + "=" * 60)
                        print(f"📤 To: {recipient_text}")
                        print(f"Sent: {send_time}")
                        
                        if WANT_ACK:
                            # Pulse LED while waiting for ACKs (up to 5 seconds)
                            # Create a callback to check if any ACKs received
                            current_msg_ids = result.get('message_ids', {})
                            
                            def check_ack_received():
                                # Re-check status during pulse - only for current batch
                                for msg_id in current_msg_ids.keys():
                                    status = ack_tracker.get_status(msg_id)
                                    if status == 'ack':
                                        return True
                                return False
                            
                            # Wait for up to 5 seconds for ACK
                            time.sleep(5)
                            
                            # Record ACK time and display status
                            ack_time = time.strftime("%H:%M:%S")
                            
                            print("\n[ACK] Checking ACK status after 5-second wait...")
                            print(f"[ACK] Tracking {len(current_msg_ids)} messages: {list(current_msg_ids.keys())}")
                            
                            # Re-check final status - only for messages sent in this batch
                            acked = []
                            nacked = []
                            pending = []
                            
                            for msg_id, node_name in current_msg_ids.items():
                                status = ack_tracker.get_status(msg_id)
                                print(f"[ACK] Message {msg_id} to {node_name}: {status}")
                                if status == 'ack':
                                    acked.append(node_name)
                                elif status == 'nak':
                                    nacked.append(node_name)
                                elif status == 'pending':
                                    pending.append(node_name)
                            
                            if acked:
                                for node_name in acked:
                                    # Get SNR for this node
                                    node_id = NODES.get(node_name)
                                    if node_id:
                                        snr, _ = get_target_node_info(node_id)
                                        snr_display = f"{snr:.1f}" if snr is not None else "--"
                                    else:
                                        snr_display = "--"
                                    
                                    print(f"Ack : {ack_time}")
                                    print(f"SNR : {snr_display}")
                                    print(f"✓ {node_name}")
                            
                            if nacked:
                                print(f"✗ NAK from: {', '.join(nacked)}")
                            
                            if pending:
                                print(f"⏳ Pending response from: {', '.join(pending)}")
                                print(f"[ACK] Still waiting for ACK from {len(pending)} node(s)")
                                # Set retry timer
                                pending_retry_time = time.time() + ACK_RETRY_TIMEOUT
                                pending_message = message
                                pending_recipients = pending
                                logger.info(f"Will retry in {ACK_RETRY_TIMEOUT} seconds at {time.strftime('%H:%M:%S', time.localtime(pending_retry_time))}")
                            
                            if not acked and not nacked and not pending:
                                print("⚠ No acknowledgments received")
                                print("[ACK] All tracked messages appear to have no response (timeout or not tracked)")
                                # Clear any pending retry since nothing is pending
                                pending_retry_time = None
                                pending_message = None
                                pending_recipients = []
                            
                            # If we got ACKs, clear pending retry
                            if acked:
                                pending_retry_time = None
                                pending_message = None
                                pending_recipients = []
                                # Update ACK status for next message
                                LAST_ACK_STATUS = "A"
                            elif nacked or pending:
                                # Update ACK status for next message
                                LAST_ACK_STATUS = "U"
                        else:
                            # ACK disabled - just show message sent
                            print(f"✓ Message sent")
                            # No ACK tracking when disabled
                            LAST_ACK_STATUS = None
                        
                        print("=" * 60)
                        
                        # Log node data after sending message
                        log_node_data()

                
                # Auto-save CSV log every AUTO_SAVE_INTERVAL seconds
                if time.time() - last_csv_save >= AUTO_SAVE_INTERVAL:
                    save_csv_log()
                    cleanup_old_logs()
                    
            else:
                # Display * when sensor fails, show last known reading (only log, don't print)
                if last_temperature_f is not None and last_humidity is not None:
                    logger.debug(f"* Temperature: {last_temperature_f:.1f}°F (last reading)")
                    logger.debug(f"* Humidity: {last_humidity:.1f}% (last reading)")
                else:
                    logger.debug("* No sensor data available yet")
            
            # Calculate seconds until next message (next whole minute)
            seconds_until_next = 60 - current_second
            if current_second == 0:
                seconds_until_next = 60  # Just sent, next is in 60 seconds
            
            # Display countdown on one line (overwrite with \r)
            # Show temperature and humidity in the countdown
            if last_temperature_f is not None and last_humidity is not None:
                print(f"\rT: {last_temperature_f:.1f}°F  H: {last_humidity:.1f}%  Next message in {seconds_until_next}s    ", end='', flush=True)
            else:
                print(f"\rNext message in {seconds_until_next} seconds...  ", end='', flush=True)
            
            # Wait 1 second between readings to catch the whole minute
            time.sleep(1)
    
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
