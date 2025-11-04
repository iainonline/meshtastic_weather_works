# ACK Debug Logging Guide

## Overview
The weather station now includes comprehensive ACK debug logging to help diagnose ACK delivery issues. All ACK-related events are logged to `ack_debug.log` with detailed information.

## Log File Location
`ack_debug.log` - Created in the same directory as `ws4m.py`

## Event Types Logged

### 1. CALLBACK REGISTERED
**When:** At startup when ACK tracking is initialized
**Info:** Confirms callback is registered with Meshtastic interface
```
============================================================
CALLBACK REGISTERED
Callback function: <bound method AckTracker.on_ack_nak...>
WANT_ACK: True
Interface: <meshtastic.serial_interface.SerialInterface...>
============================================================
```

### 2. REGISTER
**When:** Each time a message is sent to a node
**Info:** Message ID, destination node, SNR, pending count
```
REGISTER - msg_id: 2171152396, node: yang, snr: 9.5, pending_count: 1
```

### 3. ACK CALLBACK TRIGGERED
**When:** Each time the ACK callback is invoked (incoming packet)
**Info:** Packet details, tracking status, ACK type determination
```
============================================================
ACK CALLBACK TRIGGERED
Packet format: DICT - request_id: 2171152396, from_node: 123456789, error: NONE
CALLBACK - request_id: 2171152396, from_node: 123456789, error: NONE
Tracking status - request_id: 2171152396, is_tracked: True, pending_count: 1
PROCESSING - msg_id: 2171152396, node: yang
ACK TYPE CHECK - from_node: 123456789, local_num: 987654321
REAL ACK - msg_id: 2171152396, node: yang, from_node: 123456789, time: 14:23:45
CONFIRMATION SCHEDULED - node: yang, wait_time: 30s, snr: 9.5
```

### 4. IMPLICIT ACK
**When:** Local node queued the message (not delivery confirmed)
**Info:** Message ID, node, from_node (equals local_num)
```
IMPLICIT ACK - msg_id: 2171152396, node: yang, from_node: 987654321
```

### 5. NAK
**When:** Message delivery failed with error
**Info:** Message ID, node, error reason
```
NAK - msg_id: 2171152396, node: yang, reason: MAX_RETRANSMIT
```

### 6. NOT TRACKED
**When:** Received ACK for message not in tracking (timeout/already processed)
**Info:** Message ID, list of currently pending messages
```
NOT TRACKED - msg_id: 2171152396 not in pending messages
Current pending messages: [1234567890, 9876543210]
```

### 7. STATUS
**When:** Status is queried for a message (during countdown)
**Info:** Message ID, status result (ack/nak/impl_ack/pending/unknown)
```
STATUS - msg_id: 2171152396, result: ack
```

### 8. CLEANUP
**When:** Periodic cleanup of timed-out messages
**Info:** Timeout value, expired count, pending count before/after
```
CLEANUP - timeout: 60s, expired_count: 2, pending_before: 5
TIMEOUT - msg_id: 1111111111, node: ying, timeout: 60s
TIMEOUT - msg_id: 2222222222, node: yang, timeout: 60s
CLEANUP COMPLETE - pending_after: 3
```

### 9. SEND_CONFIRMATION
**When:** Sending ACK confirmation message back to sender
**Info:** Destination node, SNR, message content, send result
```
SEND_CONFIRMATION - node: yang, snr: 9.5
SENDING - to: yang, message: hub ack | 12/24 14:23:45 | SNR: 9.5
SENT SUCCESS - to: yang
```

### 10. Errors and Exceptions
**When:** Any error occurs in ACK processing
**Info:** Exception type, message, full traceback
```
CALLBACK EXCEPTION - AttributeError: 'dict' object has no attribute 'from_id'
Traceback:
  File "/home/iain/WS/ws4m.py", line 215, in on_ack_nak
    from_node = packet.from_id
AttributeError: 'dict' object has no attribute 'from_id'
```

## Analyzing the Logs

### Normal ACK Flow (Real ACK)
```
REGISTER - msg_id: 123, node: yang, snr: 9.5, pending_count: 1
============================================================
ACK CALLBACK TRIGGERED
CALLBACK - request_id: 123, from_node: 456, error: NONE
Tracking status - is_tracked: True
PROCESSING - msg_id: 123, node: yang
ACK TYPE CHECK - from_node: 456, local_num: 789
REAL ACK - msg_id: 123, node: yang, time: 14:23:45
CONFIRMATION SCHEDULED - node: yang, wait_time: 30s
```

### Normal ACK Flow (Implicit ACK only)
```
REGISTER - msg_id: 123, node: yang, snr: 9.5, pending_count: 1
============================================================
ACK CALLBACK TRIGGERED
CALLBACK - request_id: 123, from_node: 789, error: NONE
Tracking status - is_tracked: True
PROCESSING - msg_id: 123, node: yang
ACK TYPE CHECK - from_node: 789, local_num: 789
IMPLICIT ACK - msg_id: 123, node: yang, from_node: 789
```
**Note:** If from_node equals local_num, it's only an implicit ACK (queued locally)

### No ACK Received (Timeout)
```
REGISTER - msg_id: 123, node: yang, snr: 9.5, pending_count: 1
(no callback events)
CLEANUP - timeout: 60s, expired_count: 1, pending_before: 1
TIMEOUT - msg_id: 123, node: yang, timeout: 60s
CLEANUP COMPLETE - pending_after: 0
```

### Callback Not Firing
If you see REGISTER events but NO "ACK CALLBACK TRIGGERED" events at all:
- Callback not registered correctly
- Meshtastic interface issue
- Check for "CALLBACK REGISTERED" at startup

### Message Not Tracked
```
ACK CALLBACK TRIGGERED
NOT TRACKED - msg_id: 123 not in pending messages
```
**Possible causes:**
- Message already timed out before ACK arrived
- Message already processed
- Duplicate ACK packet

## Troubleshooting Steps

### 1. Verify Callback Registration
Look for this at startup:
```
============================================================
CALLBACK REGISTERED
Callback function: <bound method...>
WANT_ACK: True
============================================================
```
If missing, check `config.ini`: `want_ack = on`

### 2. Check Message Flow
1. Find REGISTER event for your message
2. Look for ACK CALLBACK TRIGGERED with same request_id
3. Check time difference between events

### 3. Identify ACK Type
Compare `from_node` with `local_num` in ACK TYPE CHECK:
- **Same number:** Implicit ACK (local queue only)
- **Different number:** Real ACK (delivery confirmed)

### 4. Check for Errors
Search log for:
- `EXCEPTION`
- `ERROR`
- `FAILED`
- `NOT TRACKED`

### 5. Timing Analysis
- Normal ACK: < 5 seconds from REGISTER to REAL ACK
- Timeout: 60 seconds (default ACK_RETRY_TIMEOUT)
- Implicit ACK usually immediate (< 1 second)

## Configuration Impact

### config.ini Settings
```ini
[meshtastic]
want_ack = on              # Must be 'on' for any ACK tracking
ack_wait_time = 30         # Delay before sending confirmation
ack_retry_timeout = 60     # Message timeout (cleanup)
mesh_send_mode = mesh      # 'mesh' or 'direct'
pki_encrypted = on         # PKI encryption enabled
```

### Expected Behavior by Mode

**mesh mode:**
- May see multiple CALLBACK events (mesh hops)
- Longer ACK delays possible
- Check for implicit ACKs from intermediate nodes

**direct mode:**
- Should see single CALLBACK event
- Faster ACK delivery
- No intermediate hops

**PKI encrypted:**
- ACKs still work normally
- Check error_reason for PKI failures

## Common Issues and Solutions

### Issue: Only Implicit ACKs
**Symptoms:** IMPLICIT ACK events, but no REAL ACK
**Cause:** Destination node not responding
**Check:**
- Is destination node online?
- Is channel/encryption correct?
- Try `direct` mode to bypass mesh

### Issue: No Callbacks at All
**Symptoms:** REGISTER events, but no ACK CALLBACK TRIGGERED
**Cause:** Callback not firing
**Check:**
- CALLBACK REGISTERED at startup?
- WANT_ACK = True?
- Meshtastic interface connected?

### Issue: Message Not Tracked
**Symptoms:** NOT TRACKED warnings
**Cause:** ACK arrives after timeout
**Solutions:**
- Increase `ack_retry_timeout` in config.ini
- Check network latency
- Verify message IDs match

### Issue: Parse Errors
**Symptoms:** CALLBACK EXCEPTION with AttributeError
**Cause:** Packet format mismatch
**Check:**
- Meshtastic Python library version
- Packet format (dict vs protobuf)
- Check CALLBACK EXCEPTION traceback

## Log Analysis Example

If you're getting implicit ACKs but no real ACKs:

1. Search for your message ID in the log
2. Find the REGISTER event
3. Look for ACK CALLBACK TRIGGERED events
4. Check the ACK TYPE CHECK line:
   ```
   ACK TYPE CHECK - from_node: 987654321, local_num: 987654321
   ```
   If from_node == local_num → Only local queue ACK
   
5. Check if real ACK arrives later (different from_node)
6. If no real ACK after 60s, look for TIMEOUT event

## Log Retention

The `ack_debug.log` file grows over time. Consider:
- Rotating logs periodically
- Archiving old logs
- Analyzing specific time ranges
- Using grep to filter events:
  ```bash
  grep "REAL ACK" ack_debug.log
  grep "msg_id: 2171152396" ack_debug.log
  grep "EXCEPTION" ack_debug.log
  ```

## Next Steps

After collecting logs:
1. Share relevant log excerpts for analysis
2. Check for patterns (always implicit, never callbacks, etc.)
3. Test with different nodes to isolate issue
4. Compare mesh vs direct mode logs
5. Verify node IDs and PKI configuration
