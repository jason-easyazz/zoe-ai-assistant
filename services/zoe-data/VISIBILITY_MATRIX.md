# Zoe Data Visibility Matrix

Multi-user data access rules. All routers enforce these via the SQL fragment:
`(visibility = 'family' OR user_id = ?) AND deleted = 0`

## Matrix

| Table             | Default Visibility | Who Can Read                            | Who Can Write | On Delete                              |
|-------------------|--------------------|-----------------------------------------|---------------|----------------------------------------|
| **events**        | `family`           | Family (if family), creator (if personal) | Creator       | Soft-delete, others notified           |
| **lists**         | `family`           | All family members                      | All family    | Soft-delete, others notified           |
| **list_items**    | Inherits list      | Same as parent list                     | All family    | Removed for all                        |
| **people**        | `family`           | All family members                      | All family    | Soft-delete, confirm with family       |
| **reminders**     | `personal`         | Creator only                            | Creator only  | Hard delete                            |
| **notes**         | `personal`         | Creator only                            | Creator only  | Hard delete                            |
| **journal_entries** | `personal`       | Creator only                            | Creator only  | Hard delete                            |
| **transactions**  | `family`           | All family members                      | All family    | Soft-delete                            |
| **trust_allowlist** | N/A              | Admin only                              | Admin only    | Hard delete                            |
| **trust_audit**   | N/A                | Admin only                              | System only   | Never delete                           |
| **users**         | N/A                | Self (own profile), admin (all)         | Self, admin   | Admin only                             |

## Rules

1. Every record has `user_id` (creator) and `visibility` (`family` or `personal`)
2. Family-visible: all authenticated family members can read
3. Personal: only the creator can read
4. The AI infers visibility from context ("my dentist" = personal, "family dinner" = family)
5. Users can override: "add a private note..." forces `personal`
6. Soft-deleted records keep `deleted=1`, others see "deleted by" notification
7. Trust audit log is append-only (security)

## Enforcement

All routers use `get_current_user()` dependency and apply the visibility filter:
```sql
WHERE (visibility = 'family' OR user_id = ?) AND deleted = 0
```

Auth is via `X-User-Id` header (trusted from nginx/gateway, not user-settable).
