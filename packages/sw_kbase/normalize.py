"""Conservative field normalization; source values and paths always remain available."""
import re

FIELDS = {
    'dex': 'attribute.dexterity', 'dexterity': 'attribute.dexterity', 'dexterite': 'attribute.dexterity',
    'kno': 'attribute.knowledge', 'knowledge': 'attribute.knowledge', 'savoir': 'attribute.knowledge',
    'mec': 'attribute.mechanical', 'mech': 'attribute.mechanical', 'mechanical': 'attribute.mechanical',
    'per': 'attribute.perception', 'perception': 'attribute.perception',
    'str': 'attribute.strength', 'strength': 'attribute.strength', 'vigueur': 'attribute.strength',
    'tec': 'attribute.technical', 'tech': 'attribute.technical', 'technical': 'attribute.technical',
    'hull': 'hull', 'shields': 'shields', 'maneuverability': 'maneuverability',
    'space': 'speed.space', 'move': 'speed.move', 'damage': 'damage', 'cost': 'cost', 'price': 'cost',
    'scale': 'scale', 'availability': 'availability', 'avail': 'availability',
    'forcepoints': 'points.force', 'darksidepoints': 'points.dark_side', 'characterpoints': 'points.character',
    'control': 'force.control', 'sense': 'force.sense', 'alter': 'force.alter',
}


def dice(value):
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r'\s*(\d{1,3})[dD](?:\s*\+\s*([012]))?\s*', value)
    if not match:
        return None
    count, pips = int(match[1]), int(match[2] or 0)
    return {'dice': count, 'pips': pips, 'total_pips': count * 3 + pips, 'raw': value}


def fields(payload):
    result = []
    def walk(value, path='', depth=0):
        if depth > 5 or not isinstance(value, dict):
            return
        for key, raw in value.items():
            original = path + '/' + str(key).replace('~', '~0').replace('/', '~1')
            clean = re.sub('[^a-z]', '', str(key).casefold())
            canonical = FIELDS.get(clean)
            if canonical and not isinstance(raw, (dict, list)):
                parsed = dice(raw)
                result.append({'field': canonical, 'value': parsed or raw, 'source_path': original,
                               'unit': 'dice' if parsed else 'source_value'})
            # Only recurse known stat containers. Nested weapons are profile components, not hull stats.
            if clean in {'stats', 'attrs', 'attributes', 'system', 'data', 'force'}:
                walk(raw, original, depth + 1)
    walk(payload)
    return result
