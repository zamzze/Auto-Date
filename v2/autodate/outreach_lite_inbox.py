import re

from .domain import ReviewRequired
from .meyo_semantics import invitation_label, resource_name, semantic_label
from .outreach import SYSTEM_NAMES, SYSTEM_ROLES
from .ui import INBOX_TIME_IDS, PACKAGE, TIME_LABEL, InboxRow, bounds, flag, label, single, without_celsius_indicator


BADGE_TEXT = re.compile(r"[1-9][0-9]{0,2}\+?")
BADGE_IDS = {"unread_badge", "unread_count", "message_badge", "conversation_unread_count"}
NAME_IDS = {"conversation_name", "chat_name", "contact_name", "conversation_title"}


def merged_badge_cell(box, children):
    # On MeYo the merged unread label has an avatar at the left and a compact icon at the upper right.
    icons = [bounds(child) for child in children if child.get("class") == "android.widget.ImageView"
             and not label(child) and not flag(child, "clickable")]
    avatars = [item for item in icons if item.left < box.left + box.width * .12
               and .11 * box.width <= item.width <= .20 * box.width]
    counters = [item for item in icons if item.right >= box.left + box.width * .90
                and .045 * box.width <= item.width <= .085 * box.width
                and .85 * item.width <= item.height <= 1.15 * item.width
                and item.top < box.top + box.height * .5]
    return len(avatars) == len(counters) == 1


def read_row(node, box):
    parts = label(node).strip().splitlines()
    role = resource_name(node)
    if not parts or role in BADGE_IDS or role in INBOX_TIME_IDS:
        return None
    children = [child for child in node.iter("node") if child is not node and child.get("package") == PACKAGE
                and child.get("visible-to-user") != "false" and (child_box := bounds(child)) and box.contains(child_box)]
    names = {label(child).strip() for child in children if resource_name(child) in NAME_IDS and label(child).strip()}
    badges = {label(child).strip() for child in children if resource_name(child) in BADGE_IDS
              and BADGE_TEXT.fullmatch(label(child).strip())}
    if len(names) > 1 or len(badges) > 1:
        return None

    unread = 0
    first = parts[0].strip()
    if len(parts) > 1 and first in badges and first not in names:
        unread = int(parts.pop(0).strip().rstrip("+"))
    elif len(parts) >= 4 and BADGE_TEXT.fullmatch(first) and not names:
        if not TIME_LABEL.fullmatch(parts[-1].strip()) or not merged_badge_cell(box, children):
            return None  # A numeric name with a multiline preview is otherwise indistinguishable here.
        unread = int(parts.pop(0).strip().rstrip("+"))
    alias = parts[0].strip()
    if not alias or (names and alias not in names):
        return None
    if semantic_label(alias) in SYSTEM_NAMES or role in SYSTEM_ROLES:
        return InboxRow(alias, "", "", unread, box), True
    if len(parts) == 1 and not names and (BADGE_TEXT.fullmatch(alias) or TIME_LABEL.fullmatch(alias)):
        return None
    if len(parts) == 2 and not names:
        return None  # Missing a verifiable preview/time layout.

    footer = parts[-1].strip()
    time_label = footer if len(parts) >= 3 and (TIME_LABEL.fullmatch(footer) or any(
        resource_name(child) in INBOX_TIME_IDS and label(child).strip() == footer for child in children)) else ""
    if len(parts) >= 3 and BADGE_TEXT.fullmatch(alias) and not names and not time_label:
        return None
    raw = "\n".join(parts[1:-1] if time_label else parts[1:]).strip()
    preview = without_celsius_indicator(raw, children)
    row = InboxRow(alias, preview, time_label, unread or int(next(iter(badges), "0").rstrip("+")), box,
                   invitation_label(preview, require_brackets=True), raw if preview != raw else None)
    return row, False


def inbox_page(snapshot):
    area = snapshot.inbox_area()
    if area is None:
        raise ReviewRequired("inbox_not_visible")
    container = single([node for node in snapshot.nodes if bounds(node) == area and flag(node, "scrollable")
                        and not label(node)], "inbox_list_ambiguous")

    def rows(parent):
        for node in parent:
            box = bounds(node)
            if (node.get("package") != PACKAGE or node.get("visible-to-user") == "false" or not box):
                continue
            if (box.width >= area.width * .90 and area.top < box.point()[1] < area.bottom
                    and flag(node, "clickable") and flag(node, "scrollable") and label(node)):
                if item := read_row(node, box):
                    yield item
                continue  # Descendants of this cell describe it and cannot be separate recipients.
            yield from rows(node)

    return area, sorted(rows(container), key=lambda item: item[0].bounds.top)
