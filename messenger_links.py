"""Remove verified Telegram bindings by their actual database keys."""


def matches(key, row, telegram_id):
    if not isinstance(row, dict):
        return False
    owner = row.get("tgUserId") or row.get("telegramUserId") or row.get("tg_user_id")
    if owner is None and str(key).isdigit() and (row.get("siteUserId") or row.get("uid")):
        owner = key
    return owner is not None and str(owner) == str(telegram_id)


def unlink(ref, telegram_id):
    removed = []
    def update(rows):
        removed.clear()
        if not rows:
            return rows
        keep = {}
        for key, row in rows.items():
            if matches(key, row, telegram_id):
                removed.append(row)
            else:
                keep[key] = row
        return keep
    # Re-evaluate on Firebase transaction retries; never remove a reassigned key.
    if ref.get():
        ref.transaction(update)
    return removed
