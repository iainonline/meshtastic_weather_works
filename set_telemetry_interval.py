#!/usr/bin/env python3
"""
Set Meshtastic Telemetry Interval
==================================
Simple command-line utility to configure the telemetry module's
device and environment update intervals on a Meshtastic device.

Default setting: 60 seconds (sends telemetry every minute)

Usage:
    python set_telemetry_interval.py [seconds]
    
Examples:
    python set_telemetry_interval.py          # Sets to 60 seconds (default)
    python set_telemetry_interval.py 300      # Sets to 300 seconds (5 minutes)
    python set_telemetry_interval.py 0        # Disables device & environment telemetry
"""

import sys
import time
import meshtastic
import meshtastic.serial_interface

def set_telemetry_interval(interval_seconds=60):
    """
    Set the device and environment telemetry update intervals.
    
    Args:
        interval_seconds (int): Update interval in seconds. 
                               Default is 60 seconds.
                               Set to 0 to disable.
    """
    print("=" * 70)
    print("MESHTASTIC TELEMETRY INTERVAL CONFIGURATION")
    print("=" * 70)
    print()
    
    try:
        print("📡 Connecting to Meshtastic device via USB...")
        interface = meshtastic.serial_interface.SerialInterface()
        print("✅ Connected successfully")
        print()
        
        # Get device info
        if hasattr(interface, 'myInfo') and interface.myInfo:
            node_id = interface.myInfo.my_node_num
            print(f"Device Node ID: {node_id} (!{node_id:08x})")
        
        # Wait for config to be received
        print("⏳ Waiting for device configuration...")
        time.sleep(3)
        
        # Access the local node
        local_node = interface.localNode
        
        # Get current settings
        print()
        print("📊 CURRENT Telemetry Settings:")
        print("-" * 70)
        
        current_device = local_node.moduleConfig.telemetry.device_update_interval
        current_env = local_node.moduleConfig.telemetry.environment_update_interval
        
        print(f"  Device Update Interval:      {current_device} seconds")
        print(f"  Environment Update Interval: {current_env} seconds")
        
        print()
        print("-" * 70)
        print()
        
        # Check if changes are needed
        if current_device == interval_seconds and current_env == interval_seconds:
            print(f"ℹ️  Both intervals are already set to {interval_seconds} seconds")
            print("   No changes needed.")
            interface.close()
            return
        
        # Show what will change
        print("📝 Changes to be made:")
        if current_device != interval_seconds:
            print(f"   Device:      {current_device}s → {interval_seconds}s")
        else:
            print(f"   Device:      {current_device}s (no change)")
        if current_env != interval_seconds:
            print(f"   Environment: {current_env}s → {interval_seconds}s")
        else:
            print(f"   Environment: {current_env}s (no change)")
        print()
        
        # Set new intervals
        if interval_seconds == 0:
            print(f"⚙️  Disabling device and environment telemetry...")
        else:
            print(f"⚙️  Setting device and environment telemetry intervals to {interval_seconds} seconds...")
        
        # Update the values
        local_node.moduleConfig.telemetry.device_update_interval = interval_seconds
        local_node.moduleConfig.telemetry.environment_update_interval = interval_seconds
        
        # Write to device
        local_node.writeConfig("telemetry")
        
        print("✅ Configuration sent to device")
        print()
        print("⏳ Waiting for device to apply settings...")
        time.sleep(3)
        
        # Verify the change
        print()
        print("📊 NEW Telemetry Settings:")
        print("-" * 70)
        
        new_device = local_node.moduleConfig.telemetry.device_update_interval
        new_env = local_node.moduleConfig.telemetry.environment_update_interval
        
        print(f"  Device Update Interval:      {new_device} seconds")
        print(f"  Environment Update Interval: {new_env} seconds")
        
        print()
        
        if new_device == interval_seconds and new_env == interval_seconds:
            print("✅ SUCCESS: Both telemetry intervals updated successfully!")
        elif new_device == interval_seconds or new_env == interval_seconds:
            print("✅ PARTIAL: Some intervals updated successfully")
            if new_device != interval_seconds:
                print(f"   ⚠️  Device interval is {new_device}s (expected {interval_seconds}s)")
            if new_env != interval_seconds:
                print(f"   ⚠️  Environment interval is {new_env}s (expected {interval_seconds}s)")
        else:
            print("⚠️  WARNING: Intervals may not have updated yet.")
            print("   Run this script again in a few seconds to verify.")
        
        print()
        print("=" * 70)
        
        if interval_seconds == 0:
            print("Device and environment telemetry are now DISABLED")
        elif interval_seconds < 60:
            print(f"Telemetry will update every {interval_seconds} seconds")
            print("⚠️  Note: Frequent updates increase battery drain and mesh traffic")
        elif interval_seconds == 60:
            print("Telemetry will update every minute (recommended)")
        else:
            minutes = interval_seconds // 60
            print(f"Telemetry will update every {minutes} minutes")
        
        print("=" * 70)
        
        interface.close()
        
    except KeyboardInterrupt:
        print("\n\n⚠️  Interrupted by user")
        sys.exit(1)
        
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        print()
        print("Troubleshooting:")
        print("  1. Ensure Meshtastic device is connected via USB")
        print("  2. Check permissions: sudo usermod -a -G dialout $USER")
        print("  3. Verify with: meshtastic --info")
        print("  4. Try unplugging and reconnecting the device")
        print("  5. Make sure the device firmware supports the telemetry module")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def main():
    """Main entry point."""
    # Parse command line argument
    interval = 60  # Default
    
    if len(sys.argv) > 1:
        try:
            interval = int(sys.argv[1])
            if interval < 0:
                print("❌ ERROR: Interval must be 0 or positive")
                print()
                print("Usage: python set_telemetry_interval.py [seconds]")
                print()
                print("Examples:")
                print("  python set_telemetry_interval.py          # 60 seconds (default)")
                print("  python set_telemetry_interval.py 300      # 300 seconds (5 minutes)")
                print("  python set_telemetry_interval.py 0        # Disable telemetry")
                sys.exit(1)
        except ValueError:
            print("❌ ERROR: Invalid number")
            print()
            print("Usage: python set_telemetry_interval.py [seconds]")
            sys.exit(1)
    
    # Run the configuration
    set_telemetry_interval(interval)


if __name__ == "__main__":
    main()
