/*
 * Zoe phase-2 firmware: bonded-only BLE.
 *
 * - Every Omi GATT characteristic and CCC (audio, storage, settings, features, time, button, haptic) and the MCUmgr SMP
 *   service require an ENCRYPTED link (Zephyr BT_GATT_PERM_*_ENCRYPT; CONFIG_MCUMGR_TRANSPORT_BT_PERM_RW_ENCRYPT). DIS and
 *   Battery stay readable, as on stock.
 * - Pairing is LE Secure Connections only (CONFIG_BT_SMP_SC_PAIR_ONLY) and is REFUSED unless the pairing window is open.
 *   The window opens on the physical gesture (zoe_gesture.h, two double-taps) or, only while the pendant holds no bond at
 *   all, for the first minutes after boot (first setup). One bond (CONFIG_BT_MAX_PAIRED=1); a new pairing replaces it.
 * - The first boot of this firmware deletes every bond made under stock firmware (a phone the Omi app paired is not
 *   trusted to read Zoe's audio).
 * - A connection that has not reached encryption within CONFIG_ZOE_UNENCRYPTED_KICK_S is dropped (outside the window), so
 *   a stranger cannot sit on the pendant's single connection and lock the Pi out.
 */
#ifndef ZOE_BLE_SEC_H
#define ZOE_BLE_SEC_H

#include <stdbool.h>
#include <zephyr/bluetooth/gatt.h>

#if defined(CONFIG_ZOE_BLE_BONDED_ONLY)
#define ZOE_PERM_R BT_GATT_PERM_READ_ENCRYPT
#define ZOE_PERM_W BT_GATT_PERM_WRITE_ENCRYPT
#else
#define ZOE_PERM_R BT_GATT_PERM_READ
#define ZOE_PERM_W BT_GATT_PERM_WRITE
#endif
#define ZOE_PERM_RW (ZOE_PERM_R | ZOE_PERM_W)

/* Call after bt_enable() and the BT settings load (transport_start), before advertising. */
int zoe_ble_sec_init(void);

/* The button's DOUBLE_TAP hook: feeds the gesture; opens the pairing window when it completes. */
void zoe_ble_sec_on_double_tap(void);

bool zoe_ble_sec_pairing_open(void);

#endif
