from dotenv import load_dotenv
from groq import Groq
import os
import json
import datetime
from zoneinfo import ZoneInfo
from sqlalchemy import select
from db import (Household, ShoppingItem, Task, Reminder, Event, Budget,
                PantryItem, ChatMessage)

load_dotenv()
client = Groq(api_key=os.environ.get("GROQ_API_KEY"), timeout=30.0)
MODEL = "openai/gpt-oss-20b"


# ---------- Tool definitions ----------
def tool(name, description, properties, required):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required},
        },
    }


def s(desc):
    return {"type": "string", "description": desc}


def b(desc):
    return {"type": "boolean", "description": desc}


TOOLS = [
    tool("add_shopping_item", "Add an item to the household shopping list",
         {"name": s("Name of the item, e.g. 'milk'")}, ["name"]),
    tool("remove_shopping_item", "Remove an item from the shopping list",
         {"name": s("Name of the item to remove")}, ["name"]),
    tool("edit_shopping_item", "Rename an item already on the shopping list",
         {"old_name": s("Current item name"), "new_name": s("New item name")},
         ["old_name", "new_name"]),

    tool("add_task",
         "Add a household chore or to-do task. If the person gives a deadline or time, "
         "include it as due_at so the task gets alarms.",
         {"title": s("Short title, e.g. 'take out trash'"),
          "due_at": s("Optional due date/time as 'YYYY-MM-DD HH:MM'")},
         ["title"]),
    tool("remove_task", "Remove/complete a task",
         {"title": s("Title (or part of it) of the task to remove")}, ["title"]),
    tool("edit_task",
         "Change an existing task's title and/or due date/time. Provide only what changes.",
         {"title": s("Current title (or part of it) of the task to edit"),
          "new_title": s("New task title (optional)"),
          "new_due_at": s("New due date/time as 'YYYY-MM-DD HH:MM' (optional)"),
          "remove_due_date": b("Set true to clear the task's due date")},
         ["title"]),

    tool("add_reminder", "Create a one-off reminder",
         {"text": s("What to be reminded about"),
          "remind_at": s("Date/time as 'YYYY-MM-DD HH:MM', e.g. '2026-09-12 09:00'")},
         ["text", "remind_at"]),
    tool("remove_reminder", "Delete/cancel a reminder",
         {"text": s("Text (or part of it) of the reminder to delete")}, ["text"]),
    tool("edit_reminder",
         "Change an existing reminder's text and/or time. Provide only the fields that change.",
         {"text": s("Current text (or part of it) of the reminder to edit"),
          "new_text": s("New reminder text (optional)"),
          "new_remind_at": s("New date/time as 'YYYY-MM-DD HH:MM' (optional)")},
         ["text"]),

    tool("add_event", "Add an event to the household calendar",
         {"title": s("Event title, e.g. 'Dentist appointment'"),
          "starts_at": s("Date/time as 'YYYY-MM-DD HH:MM', e.g. '2026-09-15 14:00'")},
         ["title", "starts_at"]),
    tool("remove_event", "Delete/cancel an event",
         {"title": s("Title (or part of it) of the event to delete")}, ["title"]),
    tool("edit_event",
         "Change an existing event's title and/or time. Provide only the fields that change.",
         {"title": s("Current title (or part of it) of the event to edit"),
          "new_title": s("New event title (optional)"),
          "new_starts_at": s("New date/time as 'YYYY-MM-DD HH:MM' (optional)")},
         ["title"]),

    {
        "type": "function",
        "function": {
            "name": "set_budget",
            "description": (
                "Set or replace the household's whole budget: a total amount, a time period "
                "(like 'week' or 'month'), and a breakdown into spending categories. When the "
                "person gives you a total amount without a breakdown, decide reasonable category "
                "amounts yourself (food is usually the largest share of a tight household budget) "
                "and call this tool with your suggested breakdown."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "total": {"type": "number", "description": "Total budget amount"},
                    "period": {"type": "string", "description": "Time period, e.g. 'week' or 'month'"},
                    "categories": {
                        "type": "object",
                        "description": "Map of category name to amount, e.g. {'food': 10000, 'transport': 7000}",
                        "additionalProperties": {"type": "number"},
                    },
                },
                "required": ["total", "period", "categories"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "adjust_budget_category",
            "description": "Change the amount for one budget category, or add a new category, e.g. 'reduce food to 7000'",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "description": "Category name, e.g. 'food'"},
                    "amount": {"type": "number", "description": "New amount for that category"},
                },
                "required": ["category", "amount"],
            },
        },
    },
    tool("remove_budget_category", "Remove a spending category from the budget",
         {"category": s("Category name to remove")}, ["category"]),

    tool("add_pantry_item", "Add or update an item in the pantry/food inventory",
         {"name": s("Item name, e.g. 'rice'"), "quantity": s("Quantity as text, e.g. '5kg' or '12'")},
         ["name"]),
    tool("remove_pantry_item", "Remove an item from the pantry, e.g. when it's used up",
         {"name": s("Item name to remove")}, ["name"]),
]


# ---------- Helpers ----------
def rows(db, Model, hid):
    return list(db.scalars(
        select(Model).where(Model.household_id == hid).order_by(Model.id)
    ).all())


def find_match(items, key, query):
    """Find one item by exact name, else substring, else all-words match.
    Returns (item, None) on success or (None, error_dict)."""
    q = str(query).strip().lower()
    exact = [it for it in items if key(it).lower() == q]
    candidates = exact or [it for it in items if q in key(it).lower()]
    if not candidates and q:
        words = q.split()
        candidates = [it for it in items if all(w in key(it).lower() for w in words)]
    if len(candidates) == 1:
        return candidates[0], None
    if not candidates:
        return None, {
            "status": "not_found",
            "searched_for": query,
            "available": [key(it) for it in items],
            "note": "Retry with one of the available names if it matches what the person meant.",
        }
    return None, {
        "status": "ambiguous",
        "matches": [key(it) for it in candidates],
        "note": "Ask the person which one they mean.",
    }


def need(args, key):
    value = str(args.get(key, "")).strip()
    if not value:
        raise ValueError(f"'{key}' can't be empty")
    return value


def bad_datetime(value):
    try:
        datetime.datetime.strptime(value, "%Y-%m-%d %H:%M")
        return None
    except Exception:
        return {"error": f"'{value}' is not a valid date/time. Use the format 'YYYY-MM-DD HH:MM'."}


def reset_flags(obj):
    if hasattr(obj, "day_before_announced"):
        obj.day_before_announced = False
        obj.hour_before_announced = False


def budget_numbers(b):
    allocated = sum((b.categories or {}).values())
    return {"allocated_total": allocated, "unallocated_amount": b.total - allocated}


# ---------- Tool execution (everything is scoped to one household) ----------
def run_tool(db, hid, name, args):
    # Shopping
    if name == "add_shopping_item":
        item = need(args, "name")
        if not any(r.name.lower() == item.lower() for r in rows(db, ShoppingItem, hid)):
            db.add(ShoppingItem(household_id=hid, name=item))
            db.commit()
        return {"status": "added", "item": item}

    if name == "remove_shopping_item":
        obj, err = find_match(rows(db, ShoppingItem, hid), lambda r: r.name, args["name"])
        if err:
            return err
        removed = obj.name
        db.delete(obj)
        db.commit()
        return {"status": "removed", "item": removed}

    if name == "edit_shopping_item":
        obj, err = find_match(rows(db, ShoppingItem, hid), lambda r: r.name, args["old_name"])
        if err:
            return err
        old = obj.name
        obj.name = need(args, "new_name")
        db.commit()
        return {"status": "updated", "from": old, "to": obj.name}

    # Tasks
    if name == "add_task":
        due = str(args.get("due_at") or "").strip()
        if due:
            err = bad_datetime(due)
            if err:
                return err
        title = need(args, "title")
        db.add(Task(household_id=hid, title=title, due_at=due))
        db.commit()
        return {"status": "added", "task": title, "due_at": due}

    if name == "remove_task":
        obj, err = find_match(rows(db, Task, hid), lambda r: r.title, args["title"])
        if err:
            return err
        removed = obj.title
        db.delete(obj)
        db.commit()
        return {"status": "removed", "task": removed}

    if name == "edit_task":
        obj, err = find_match(rows(db, Task, hid), lambda r: r.title, args["title"])
        if err:
            return err
        if args.get("remove_due_date"):
            obj.due_at = ""
        elif args.get("new_due_at"):
            err = bad_datetime(args["new_due_at"])
            if err:
                return err
            obj.due_at = args["new_due_at"]
        if args.get("new_title"):
            obj.title = need(args, "new_title")
        reset_flags(obj)
        db.commit()
        return {"status": "updated", "task": {"title": obj.title, "due_at": obj.due_at}}

    # Reminders
    if name == "add_reminder":
        err = bad_datetime(args["remind_at"])
        if err:
            return err
        text = need(args, "text")
        db.add(Reminder(household_id=hid, text=text, remind_at=args["remind_at"]))
        db.commit()
        return {"status": "added", "reminder": text, "remind_at": args["remind_at"]}

    if name == "remove_reminder":
        obj, err = find_match(rows(db, Reminder, hid), lambda r: r.text, args["text"])
        if err:
            return err
        info = {"status": "removed", "reminder": obj.text, "remind_at": obj.remind_at}
        db.delete(obj)
        db.commit()
        return info

    if name == "edit_reminder":
        if args.get("new_remind_at"):
            err = bad_datetime(args["new_remind_at"])
            if err:
                return err
        obj, err = find_match(rows(db, Reminder, hid), lambda r: r.text, args["text"])
        if err:
            return err
        if args.get("new_text"):
            obj.text = need(args, "new_text")
        if args.get("new_remind_at"):
            obj.remind_at = args["new_remind_at"]
        reset_flags(obj)
        db.commit()
        return {"status": "updated", "reminder": {"text": obj.text, "remind_at": obj.remind_at}}

    # Events
    if name == "add_event":
        err = bad_datetime(args["starts_at"])
        if err:
            return err
        title = need(args, "title")
        db.add(Event(household_id=hid, title=title, starts_at=args["starts_at"]))
        db.commit()
        return {"status": "added", "event": title, "starts_at": args["starts_at"]}

    if name == "remove_event":
        obj, err = find_match(rows(db, Event, hid), lambda r: r.title, args["title"])
        if err:
            return err
        info = {"status": "removed", "event": obj.title, "starts_at": obj.starts_at}
        db.delete(obj)
        db.commit()
        return info

    if name == "edit_event":
        if args.get("new_starts_at"):
            err = bad_datetime(args["new_starts_at"])
            if err:
                return err
        obj, err = find_match(rows(db, Event, hid), lambda r: r.title, args["title"])
        if err:
            return err
        if args.get("new_title"):
            obj.title = need(args, "new_title")
        if args.get("new_starts_at"):
            obj.starts_at = args["new_starts_at"]
        reset_flags(obj)
        db.commit()
        return {"status": "updated", "event": {"title": obj.title, "starts_at": obj.starts_at}}

    # Budget
    if name == "set_budget":
        cats = {str(k).strip(): float(v) for k, v in args["categories"].items() if str(k).strip()}
        b_row = db.get(Budget, hid) or Budget(household_id=hid)
        b_row.total = float(args["total"])
        b_row.period = need(args, "period")
        b_row.categories = cats
        db.add(b_row)
        db.commit()
        return {"status": "set", "budget": {"total": b_row.total, "period": b_row.period,
                                            "categories": cats}, **budget_numbers(b_row)}

    if name in ("adjust_budget_category", "remove_budget_category"):
        b_row = db.get(Budget, hid)
        if not b_row:
            return {"error": "No budget has been set yet"}
        cats = dict(b_row.categories or {})
        if name == "adjust_budget_category":
            cats[need(args, "category")] = float(args["amount"])
            status = "updated"
        else:
            key, err = find_match(list(cats.keys()), lambda k: k, args["category"])
            if err:
                return err
            del cats[key]
            status = "removed"
        b_row.categories = cats
        db.commit()
        return {"status": status, "budget": {"total": b_row.total, "period": b_row.period,
                                             "categories": cats}, **budget_numbers(b_row)}

    # Pantry
    if name == "add_pantry_item":
        item = need(args, "name")
        quantity = str(args.get("quantity", "") or "").strip()
        for existing in rows(db, PantryItem, hid):
            if existing.name.lower() == item.lower():
                existing.quantity = quantity
                db.commit()
                return {"status": "updated", "item": existing.name, "quantity": quantity}
        db.add(PantryItem(household_id=hid, name=item, quantity=quantity))
        db.commit()
        return {"status": "added", "item": item, "quantity": quantity}

    if name == "remove_pantry_item":
        obj, err = find_match(rows(db, PantryItem, hid), lambda r: r.name, args["name"])
        if err:
            return err
        removed = obj.name
        db.delete(obj)
        db.commit()
        return {"status": "removed", "item": removed}

    return {"error": f"unknown tool: {name}"}


# ---------- System prompt with the household's full context ----------
def household_now(household):
    try:
        zone = ZoneInfo(household.timezone)
    except Exception:
        zone = ZoneInfo("UTC")
    return datetime.datetime.now(zone)


def build_system_message(db, household, user):
    hid = household.id
    now = household_now(household)
    shopping = [r.name for r in rows(db, ShoppingItem, hid)]
    tasks = [{"title": r.title, "due_at": r.due_at} for r in rows(db, Task, hid)]
    reminders = [{"text": r.text, "remind_at": r.remind_at} for r in rows(db, Reminder, hid)]
    events = [{"title": r.title, "starts_at": r.starts_at} for r in rows(db, Event, hid)]
    pantry = [{"name": r.name, "quantity": r.quantity} for r in rows(db, PantryItem, hid)]
    b_row = db.get(Budget, hid)

    if not b_row:
        budget_text = "No budget has been set yet."
    else:
        nums = budget_numbers(b_row)
        budget_text = (
            f"{json.dumps({'total': b_row.total, 'period': b_row.period, 'categories': b_row.categories})}\n"
            f"(Calculated: categories add up to {nums['allocated_total']}, "
            f"leaving {nums['unallocated_amount']} unallocated out of the {b_row.total} total.)"
        )

    def show(items, empty):
        return empty if not items else json.dumps(items, ensure_ascii=False)

    return {
        "role": "system",
        "content": (
            f"You are a helpful household manager assistant for the '{household.name}' household. "
            f"You are talking with {user.name}; other household members share the same data. "
            f"Use tools to change lists, tasks, reminders, events, budget, and pantry. "
            f"Right now it is {now.strftime('%Y-%m-%d %H:%M')}, a {now.strftime('%A')} "
            f"(household timezone: {household.timezone}). When someone mentions a relative day "
            f"like 'Friday' or 'tomorrow', calculate the actual date based on today. Date/times "
            f"for tasks, reminders and events must be in the format 'YYYY-MM-DD HH:MM'. When a "
            f"reminder, event or task deadline doesn't include a specific time, assume a "
            f"reasonable one (like 9:00 AM) and mention that assumption in your reply.\n\n"
            f"Tasks may have an optional due date/time (due_at); a task with a due date gets "
            f"alarms just like a reminder. When someone mentions a task with a deadline "
            f"(e.g. 'I need to pay the bill by Friday 5pm'), create the task with due_at set. "
            f"Use a plain reminder only for 'remind me to...' requests.\n\n"
            f"Here is EVERYTHING currently stored for the household:\n\n"
            f"SHOPPING LIST: {show(shopping, 'empty')}\n\n"
            f"TASKS: {show(tasks, 'none')}\n\n"
            f"REMINDERS: {show(reminders, 'none')}\n\n"
            f"EVENTS: {show(events, 'none')}\n\n"
            f"BUDGET:\n{budget_text}\n\n"
            f"PANTRY: {show(pantry, 'The pantry is currently empty.')}\n\n"
            f"Answer questions about any of this directly (e.g. 'what's on this week', "
            f"'what's overdue', 'what can I eat', 'can I afford this') by reasoning over the "
            f"data above. You only need tools to actually change stored data. To cancel, delete, "
            f"rename or reschedule something, use the matching remove_ or edit_ tool rather than "
            f"adding a duplicate. If a tool reports the match was ambiguous or not found, tell "
            f"the person and ask which one they meant. Never claim you changed something unless "
            f"the tool result says it succeeded. Only discuss and manage this household's "
            f"data; politely decline unrelated requests.\n\n"
            f"FORMATTING: your replies are shown as plain text in a simple chat box that does "
            f"NOT render Markdown, so never use tables, pipe characters (|), asterisks for "
            f"bold, or headings. Write in short plain sentences and paragraphs instead. If you "
            f"need to list a few things, use simple dashes on their own lines. Money amounts "
            f"are in Nigerian Naira unless the person says otherwise: use the ₦ symbol, never $."
        ),
    }


# ---------- Chat loop ----------
def chat(db, user, user_message):
    household = db.get(Household, user.household_id)
    past = db.scalars(
        select(ChatMessage).where(ChatMessage.user_id == user.id)
        .order_by(ChatMessage.id.desc()).limit(20)
    ).all()
    history = [{"role": m.role, "content": m.content} for m in reversed(past)]
    messages = ([build_system_message(db, household, user)] + history
                + [{"role": "user", "content": user_message}])

    reply = None
    for _ in range(4):  # allows chained actions, e.g. remove then add
        response = client.chat.completions.create(model=MODEL, messages=messages, tools=TOOLS)
        msg = response.choices[0].message

        if not msg.tool_calls:
            reply = msg.content
            break

        messages.append(msg)
        for call in msg.tool_calls:
            try:
                args = json.loads(call.function.arguments or "{}")
                result = run_tool(db, household.id, call.function.name, args)
            except Exception as e:
                db.rollback()
                result = {"error": f"{type(e).__name__}: {e}"}
            messages.append({
                "role": "tool",
                "tool_call_id": call.id,
                "content": json.dumps(result, ensure_ascii=False),
            })

    reply = reply or "Done."
    db.add(ChatMessage(user_id=user.id, role="user", content=user_message))
    db.add(ChatMessage(user_id=user.id, role="assistant", content=reply))
    db.commit()
    return reply