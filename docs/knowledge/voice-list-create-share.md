---
type: spec
title: Voice list creation + "share it to the house" (ZOE_VOICE_LIST_SHARE, ZOE_LIST_SHARE_HEADSUP)
description: Acceptance spec + design record. "Create a list called X and add a, b and c to it" makes one private list with three items; "share it to the house" (or naming the list) is the only way it becomes household-visible, owner only; each other member hears "X has shared a list with you" once.
tags: [voice, lists, sharing, visibility, privacy, skybridge, spec]
timestamp: 2026-11-10T00:00:00Z
---

# Voice list creation + share to the house

## Acceptance — true when done

Owner words, 2026-11-10. Synthetic names: demo_alex is the speaker, demo_blair is another member, a guest is anyone unidentified.
Each line is checkable by a QA chat or a test; none is "(not yet)".

1. Alex says "Hey Zoe, create a list called Camping Supplies and add tent, sleeping bags and bug spray to it." Zoe makes ONE list
   named Camping Supplies with THREE items (tent, sleeping bags, bug spray) - not a list called "Camping Supplies and add tent" -
   and says so ("Created your Camping Supplies list with tent, sleeping bags and bug spray.").
2. "Start a Bunnings list" (and "make me a Bunnings list", "I want a list called Bunnings") creates a list too, and a plain
   "add paint brush, paint and tarps to my Bunnings list" puts three items on it. "Call mum and dad" on a to-do list is still ONE task.
3. A list made by voice is PRIVATE to its creator, on the shared panel too: Blair, a guest, and the touch panel do not see it.
   Nothing widens it by device or channel.
4. Alex says "Share it to the house" (or "share it with everyone", or "share the Camping Supplies list with the family"): the list
   becomes visible to every member and the panel, and Zoe says so. "It" is the list Alex just created, added to or has on screen.
5. Only the list's owner can share it. Blair saying "share the Camping Supplies list" about Alex's list is refused, and a guest
   cannot share at all. Alex's private list is never confirmed to exist to anyone else.
6. "Make it private" / "stop sharing it" reverses it (owner only). The household shopping list stays shared.
7. The next time Blair talks to Zoe, Zoe mentions once: "Alex has shared a list with you: Camping Supplies." Never to Alex, never
   to a guest, never again after it was said, and not at all if Alex made the list private first. The sharer's name comes from the
   users table.
8. The new and the shared list show on the Lists page and the touch panel for exactly the right people (private = its creator;
   shared = everyone) with no UI change: both read `GET /api/lists/{type}`, which returns family-visible lists plus the caller's own.

## Where it lives

- **Create + compound** — `skybridge_service._list_create_parts` (name, type, items) feeds `_resolve_list_create`, which makes the
  list `personal` and then adds the items through `_add_spoken_items` (the same insert the plain add uses).
- **Split rule** — `list_service.splits_items(list_type, list_name)`: split for `shopping`, `bucket` and a `personal` list the member
  named themselves (a "list of things"); never for `tasks`, `work` or the default "Personal" list (a to-do list: one spoken add is one
  task). Used by the skybridge add, `list_service.add_items_to_list` (the intent-router lane) and the create compound.
- **Private** — a voice-created list is always `visibility='personal'`. A guest never creates one (identity gate); the
  guest-started SHOPPING list stays family (`list_service.add_item_to_list`: a guest owns no users row, the household owns it).
- **Share** — `skybridge_service._list_share_from_text` -> `lists/share_list` | `lists/unshare_list`; `list_sharing.set_list_visibility`
  is the one write (owner check inside the UPDATE). "It" = the on-screen card's list, else `list_service.focus_list` (the list the
  member last created or added to, 30 minutes). With no list in play the words are not ours (`handled: false`, the brain answers).
  The existing REST `PUT /api/lists/{type}/{id}` `visibility` field is unchanged.
- **Heads-up** — `list_sharing.queue_heads_up` writes one `pending_suggestions` row per OTHER household member
  (`action_type='list_shared'`, `description` = list id, `resolved` 0 pending / 1 dropped / 2 delivered).
  `list_sharing.claim_heads_ups` runs in the recipient's next brain turn (`zoe_flue_client._list_share_block`, the contact-offer
  block's delimiters), claims each row exactly once, drops rows whose list is no longer shared, and ignores guests.

## Flags

- `ZOE_VOICE_LIST_SHARE` (default on) - the spoken share / make-private intents. Off: those phrases are not list commands.
- `ZOE_LIST_SHARE_HEADSUP` (default on) - queue and say the heads-up. This is the riskier half (it adds a block to the voice brain
  turn), so it has its own switch: off queues nothing and says nothing; the share itself still works.

## Known limits

- "Make a shopping list" does not create a list: the household has ONE (family-visible by definition), so Zoe says she already has it.
- The heads-up is at-most-once and unhurried by design: it is claimed when it enters the recipient's brain turn (a turn that fails
  after that does not repeat it), at most two per turn, and it waits for a turn no other offer/verify/emotional block occupies.
- A list name is owner-chosen text in another member's brain prompt: it is capped (48), stripped of quotes/brackets and framed as a
  label, never an instruction - the same exposure and the same defence as the contact-offer block.

- The heads-up rides a brain turn: a turn a fast path answers without the brain does not carry it; it waits for the next brain turn.
- A shared personal-type list is family-visible, so a type-only "add X to my personal list" prefers the household list of that type
  (the existing household-list rule). Naming the list is unambiguous.
