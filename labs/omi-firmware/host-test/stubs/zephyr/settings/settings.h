#ifndef STUB_ZEPHYR_SETTINGS_H
#define STUB_ZEPHYR_SETTINGS_H
#include <stddef.h>
#include <sys/types.h>
typedef ssize_t (*settings_read_cb)(void *cb_arg, void *data, size_t len);
typedef int (*settings_load_direct_cb)(const char *key, size_t len, settings_read_cb read_cb, void *cb_arg, void *param);
int settings_load_subtree_direct(const char *subtree, settings_load_direct_cb cb, void *param);
int settings_save_one(const char *name, const void *value, size_t val_len);
int settings_delete(const char *name);
#endif
