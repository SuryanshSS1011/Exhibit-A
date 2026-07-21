def active_names(users: list[dict[str, object]]) -> list[str]:
    names = []
    for user in users:
        if user["active"]:
            names.append(str(user["name"]))
    return names
