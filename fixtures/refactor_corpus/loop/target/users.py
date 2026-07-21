def active_names(users: list[dict[str, object]]) -> list[str]:
    return [str(user["name"]) for user in users if user["active"]]
