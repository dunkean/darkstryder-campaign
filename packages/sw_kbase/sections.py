"""Deterministic corpus separation and reusable source category hints."""
import re


CATEGORY_RULES = [
    (r'\b(force powers?|pouvoirs? de la force)\b','force_power'),
    (r'\b(droids?|droïdes?)\b','droid_model'),
    (r'\b(characters?|people|npcs?|personnages?|individuals?|crew members)\b','person'),
    (r'\b(species|races?|aliens?|espèces?)\b','species'),
    (r'\b(creatures?|créatures?|animals?|beasts?)\b','creature_type'),
    (r'\b(starships?|ships?|vehicles?|fighters?|space transports?|capital ships?|corvettes?|vaisseaux?|véhicules?)\b','craft_model'),
    (r'\b(weapons?|equipment|armor|armour|armes?|technologies?|équipements?)\b','equipment_model'),
    (r'\b(planets?|systems?|locations?|sectors?|moons?|places?|planètes?|secteurs?|zones spatiales)\b','location'),
    (r'\b(organizations?|factions?|organisations?)\b','organization'),
    (r'\b(skills?|compétences?)\b','skill'),
    (r'\b(templates?|archetypes?|archétypes?)\b','archetype'),
    (r'\b(adventures?|scenarios?|scénarios?)\b','scenario'),
    (r'\b(battles?|conflicts?|campaigns?|batailles?|campagnes?)\b','event'),
    (r'\b(rules?|règles?)\b','rule'),
]


def category_kind(categories, fallback='unknown'):
    text=' | '.join(categories)
    # Ignore broad navigation boxes: only supplied article taxonomy is used.
    for pattern, kind in CATEGORY_RULES:
        if re.search(pattern,text,re.I):return kind
    return fallback


def section_for(record):
    scope=record.get('scope') or {}
    if scope.get('section') in {'scenario','reference','source','metadata'}:
        return scope['section'],'source_section'
    locator=record.get('locator') or {}
    if re.match(r'^(?:Category|Template)\s*:',record['name'],re.I):return 'metadata','wiki_template_or_category'
    if locator.get('namespace') in {10,14} or record.get('namespace') in {10,14}:
        return 'metadata','wiki_template_or_category'
    if record.get('origin')=='transcribed_reference':return 'source','transcript_unit'
    if record['kind']=='scenario':return 'scenario','scenario_kind'
    if record['kind']=='event' and record.get('origin')=='phase3_extraction' and scope.get('collection') in {'darkstryder','addons'}:
        return 'scenario','campaign_event'
    return 'reference','reference_default'
