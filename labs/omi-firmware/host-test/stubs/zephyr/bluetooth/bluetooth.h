#ifndef STUB_ZEPHYR_BT_H
#define STUB_ZEPHYR_BT_H
#include <stdbool.h>
#include <stdint.h>
#define BT_ID_DEFAULT 0
typedef struct { uint8_t type; uint8_t a[6]; } bt_addr_le_t;
struct bt_bond_info { bt_addr_le_t addr; };
static inline bool bt_addr_le_eq(const bt_addr_le_t *a, const bt_addr_le_t *b)
{
    for (int i = 0; i < 6; i++) {
        if (a->a[i] != b->a[i]) return false;
    }
    return a->type == b->type;
}
void bt_foreach_bond(uint8_t id, void (*func)(const struct bt_bond_info *info, void *user_data), void *user_data);
int bt_unpair(uint8_t id, const bt_addr_le_t *addr);
#endif
