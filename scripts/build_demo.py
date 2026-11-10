"""Build the 1444 DD atlas from EU4, the Chinese language mods, and DD."""

import argparse
import csv
import json
import math
import re
from collections import defaultdict
from itertools import chain, product
from pathlib import Path

import cv2
import numpy as np
import shapely
from PIL import Image
from shapely.geometry import MultiPolygon
from panel_assets import build_panel_assets, build_shields, load_rgba, save_sprite
from panel_fonts import build_panel_fonts, panel_characters


START = (1444, 11, 11)
TOKENS = re.compile(r'"(?:\\.|[^"\\])*"|#[^\r\n]*|[{}=]|[^\s{}=#"]+')
LOC_LINE = re.compile(r'^\s*([\w.\-]+):\d*\s*"(.*)"\s*(?:#.*)?$')
ESCAPE = re.compile(r'[\x10-\x13][\s\S]{2}')
DATE = re.compile(r'\d+\.\d+\.\d+')
UNDEFINED_1252 = {0xDC00 + byte: byte for byte in (0x81, 0x8D, 0x8F, 0x90, 0x9D)}
DEFAULT_STEAMAPPS = Path(r'C:\Program Files (x86)\Steam\steamapps')


def read_text(path):
    data = path.read_bytes()
    try:
        return data.decode('utf-8-sig')
    except UnicodeDecodeError:
        return data.decode('cp1252', errors='surrogateescape').translate(UNDEFINED_1252)


def assignments(tokens):
    """Yield keys and values at the current brace level."""
    i = 0
    while i + 2 < len(tokens):
        key, equals, value = tokens[i:i + 3]
        if equals != '=' or key in ('{', '}', '='):
            i += 1
            continue
        i += 3
        if value == '{':
            start, depth = i, 1
            while i < len(tokens) and depth:
                depth += int(tokens[i] == '{') - int(tokens[i] == '}')
                i += 1
            yield key, tokens[start:i - 1]
        else:
            yield key, value.strip('"')


def entries(path):
    return assignments([token for token in TOKENS.findall(read_text(path)) if not token.startswith('#')])


def parse_date(value, fallback=None):
    return tuple(map(int, value.split('.'))) if isinstance(value, str) and DATE.fullmatch(value) else fallback


def dated_blocks(items):
    blocks = []
    for key, value in items:
        date = parse_date(key)
        if date and date <= START and isinstance(value, list):
            blocks.append((date, value))
    return sorted(blocks, key=lambda entry: entry[0])


def history_timeline(path):
    items = list(entries(path))
    return chain(items, *(assignments(block) for _, block in dated_blocks(items)))


def snapshot(path, keys, blocks=()):
    return {key: value for key, value in history_timeline(path)
            if (key in keys and isinstance(value, str)) or (key in blocks and isinstance(value, list))}


def layered_files(roots, folder, extension='txt'):
    files = {}
    for root in roots:
        files.update((path.name, path) for path in (root / folder).glob(f'*.{extension}'))
    return [files[name] for name in sorted(files)]


def history_files(roots, folder, pattern):
    files = {}
    for root in roots:
        for path in sorted((root / 'history' / folder).glob('*.txt')):
            match = re.match(pattern, path.stem)
            if match:
                files[match[1]] = path
    return files


def decode_localisation(value, line_break=' '):
    # The EU4 double-byte patch stores Unicode as escaped Windows-1252 bytes.
    # Format: https://github.com/matanki-saito/EU4SpecialEscape
    def decode(match):
        marker, low, high = match[0]
        low = low.encode('cp1252')[0] if ord(low) > 255 else ord(low)
        high = high.encode('cp1252')[0] if ord(high) > 255 else ord(high)
        low -= 14 if ord(marker) & 1 else 0
        high += 9 if ord(marker) & 2 else 0
        code = (high & 255) * 256 + (low & 255)
        return chr(code - 0xE000 if 0xE100 < code < 0xEA00 else code)

    try:
        value = ESCAPE.sub(decode, value)
    except UnicodeEncodeError:
        return ''
    value = re.sub(r'§.', '', value)
    return value.replace('\\n', line_break).replace('\\"', '"').strip()


def load_localisation(roots, keys, pattern='*l_english*.yml', line_break=' '):
    strings = {}
    for root in roots:
        for subdir in ('localisation', 'localisation/l_english', 'localisation/replace'):
            for file in sorted((root / subdir).glob(pattern)):
                for line in read_text(file).split('\n'):
                    match = LOC_LINE.match(line.rstrip('\r'))
                    if match and match[1] in keys:
                        value = decode_localisation(match[2], line_break)
                        if value and not re.search(r'[\x00-\x09\x0b-\x1f\ud800-\udfff\ufffd]', value):
                            strings[match[1]] = value
    return strings


def recommended_tags(mod):
    path = mod / 'events/DD_common_events.txt'
    for kind, block in entries(path):
        if kind != 'country_event' or not isinstance(block, list):
            continue
        event = dict(assignments(block))
        if event.get('id') != 'DD_common.2':
            continue
        trigger = naming_trigger(event.get('trigger', []))
        # DD_common.2 fires only for tags outside the negated OR.
        match trigger:
            case (('not', (('or', tags),)),) if isinstance(tags, tuple) and tags:
                if all(key == 'tag' and isinstance(tag, str) and re.fullmatch(r'[A-Z0-9]{3}', tag)
                       for key, tag in tags):
                    # DD exempts Venice from the warning because it has missions.
                    return {tag for _, tag in tags} - {'VEN'}
    raise ValueError(f'Cannot read the recommendation list in {path} (DD_common.2).')


def diplomacy_records(roots):
    for file in layered_files(roots, 'history/diplomacy'):
        for kind, block in entries(file):
            if not isinstance(block, list):
                continue
            data = dict(assignments(block))
            first, second = data.get('first'), data.get('second')
            if not first or not second or first == second:
                continue
            start = parse_date(data.get('start_date'), (0, 1, 1))
            end = parse_date(data.get('end_date'), (9999, 1, 1))
            if start <= START < end:
                yield kind, first, second, data.get('subject_type', kind)


def war_records(roots, active_tags):
    wars, truces = defaultdict(set), {}
    vanilla, *_, mod = roots
    defines = [vanilla / 'common/defines.lua', *layered_files(roots, 'common/defines', 'lua')]
    years = 5
    for file in defines:
        values = re.findall(r'\bTRUCE_YEARS\s*=\s*(\d+)', read_text(file))
        if values:
            years = int(values[-1])
    # DD declares replace_path="history/wars" in descriptor.mod.
    for file in layered_files((mod,), 'history/wars'):
        items = list(entries(file))
        name = decode_localisation(dict(items).get('name', ''))
        attackers, defenders = set(), set()
        previous = set()
        for date, block in dated_blocks(items):
            for action, tag in assignments(block):
                if not isinstance(tag, str) or tag not in active_tags:
                    continue
                if action == 'add_attacker':
                    attackers.add(tag)
                elif action == 'add_defender':
                    defenders.add(tag)
                elif action == 'rem_attacker':
                    attackers.discard(tag)
                elif action == 'rem_defender':
                    defenders.discard(tag)
            opposing = {tuple(sorted((a, b))) for a, b in product(attackers, defenders) if a != b}
            until = (date[0] + years, *date[1:])
            if until > START:
                for pair in sorted(previous - opposing):
                    truces[pair] = max(truces.get(pair, until), until)
            previous = opposing
        for pair in sorted(previous):
            names = wars[pair]
            if name:
                names.add(name)
    return wars, {pair: date for pair, date in truces.items() if pair not in wars}


GOVERNMENT_FIELDS = ('rank', 'ruler_male', 'ruler_female')
GOVERNMENT_RANKS = ('1', '2', '3')
# Map estimate for opening ranks still missing after the history and reform lookups.
ESTIMATED_KINGDOM_DEVELOPMENT = 60
GOVERNMENT_FALLBACK = {
    'monarchy': ('君主制', 'Monarchy'), 'republic': ('共和国', 'Republic'),
    'theocracy': ('神权国', 'Theocracy'), 'tribal': ('部落政体', 'Tribal Government'),
    'native': ('原住民政体', 'Native Government'),
}


def definitions(roots, folder):
    for path in layered_files(roots, folder):
        for key, block in entries(path):
            if isinstance(block, list):
                yield key, dict(assignments(block))


def group_members(roots, folder):
    return {key: group for group, members in definitions(roots, folder)
            for key, block in members.items() if isinstance(block, list)}


def country_snapshot(path):
    values = {}
    reforms = {}
    flags, modifiers, past_reforms = set(), set(), set()
    for key, value in history_timeline(path):
        if key in {'government', 'government_rank', 'capital', 'religion', 'primary_culture', 'elector',
                   'monarch', 'heir'}:
            values[key] = value
        elif key == 'add_government_reform':
            reforms[value] = None
            past_reforms.add(value)
        elif key == 'remove_government_reform':
            reforms.pop(value, None)
        elif key == 'set_country_flag':
            flags.add(value)
        elif key == 'clr_country_flag':
            flags.discard(value)
        elif key == 'add_country_modifier' and isinstance(value, list):
            modifiers.add(dict(assignments(value)).get('name'))
        elif key == 'remove_country_modifier':
            modifiers.discard(value)
    return dict(values, reforms=tuple(reforms), past_reforms=past_reforms, flags=flags, modifiers=modifiers)


def province_snapshot(roots):
    files = history_files(roots, 'provinces', r'^(\d+)\s*-')
    fields = {'owner', 'hre', 'is_city', 'base_tax', 'base_production', 'base_manpower'}
    return {province: snapshot(path, fields) for province, path in files.items()}


def listed_provinces(block):
    # Province IDs are bare tokens. Nested color blocks contain RGB numbers.
    depth = 0
    for index, token in enumerate(block):
        if token == '{':
            depth += 1
        elif token == '}':
            depth -= 1
        elif depth == 0 and token.isdigit() and (index == 0 or block[index - 1] != '='):
            yield token


def capital_geography(roots):
    files = {path.name: path for path in layered_files(roots, 'map')}
    areas = {province: area for area, block in entries(files['area.txt'])
             if isinstance(block, list) for province in listed_provinces(block)}
    regions = {area: region for region, block in entries(files['region.txt'])
               if isinstance(block, list) for area in dict(assignments(block)).get('areas', [])}
    superregions = {region: superregion for superregion, block in entries(files['superregion.txt'])
                    if isinstance(block, list) for region in block}
    continents = {province: continent for continent, block in entries(files['continent.txt'])
                  if isinstance(block, list) for province in listed_provinces(block)}
    return areas, regions, superregions, continents


def naming_trigger(block):
    return tuple((key.lower(), naming_trigger(value) if isinstance(value, list) else value)
                 for key, value in assignments(block))


class GovernmentNames:
    """Infer the names visible at the start date from the installed definitions."""

    def __init__(self, roots, histories, countries, provinces, relations):
        # Resolve same-file overrides before evaluating names in file and declaration order.
        self.rules = [
            (naming_trigger(rule.get('trigger', [])),
             {field: dict(assignments(rule.get(field, []))) for field in GOVERNMENT_FIELDS})
            for _, rule in definitions(roots, 'common/government_names')
        ]
        self.localisation_keys = {key for _, fields in self.rules for ranks in fields.values() for key in ranks.values()}
        reforms = dict(definitions(roots, 'common/government_reforms'))
        basic_reforms = {government: fields.get('basic_reform')
                         for government, fields in definitions(roots, 'common/governments')}
        culture_groups = group_members(roots, 'common/cultures')
        religion_groups = group_members(roots, 'common/religions')
        self.scripted = {}
        for path in layered_files(roots, 'common/scripted_triggers'):
            for key, block in entries(path):
                if key == 'is_revolutionary_republic_trigger' and isinstance(block, list):
                    self.scripted[key.lower()] = naming_trigger(block)
        self.emperor_rank = self._emperor_rank(roots)
        areas, regions, superregions, continents = capital_geography(roots)
        diplomacy = {path.name: path for path in layered_files(roots, 'history/diplomacy')}
        emperor = snapshot(diplomacy['hre.txt'], {'emperor'}).get('emperor')
        celestial = snapshot(diplomacy['celestial_empire.txt'], {'celestial_emperor'}).get('celestial_emperor')
        subjects = {second: (first, subtype, kind) for kind, first, second, subtype in relations
                    if kind in {'vassal', 'union', 'march', 'dependency'} and first in countries and second in countries}
        cities, development = defaultdict(int), defaultdict(float)
        for province in provinces.values():
            owner = province.get('owner')
            cities[owner] += province.get('is_city') == 'yes'
            development[owner] += sum(float(province.get(key, '0')) for key in
                                      ('base_tax', 'base_production', 'base_manpower'))

        vanilla_history = history_files((roots[0],), 'countries', r'^([A-Z0-9]{3})\s*-')
        self.states = {}
        for tag, country in countries.items():
            government = country.get('government')
            active_reforms = tuple(filter(None, (basic_reforms.get(government), *country.get('reforms', ()))))
            fixed_ranks = [reforms.get(key, {}).get('fixed_rank') for key in reversed(active_reforms)]
            rank = next((rank for rank in fixed_ranks if rank in GOVERNMENT_RANKS), country.get('government_rank'))
            if not rank and tag in vanilla_history and histories.get(tag) != vanilla_history[tag]:
                base = snapshot(vanilla_history[tag], {'government', 'government_rank'})
                if government == base.get('government'):
                    rank = base.get('government_rank')
            if rank not in GOVERNMENT_RANKS:
                rank = '2' if development[tag] >= ESTIMATED_KINGDOM_DEVELOPMENT else '1'
            capital = country.get('capital')
            area = areas.get(capital)
            region = regions.get(area)
            self.states[tag] = {
                **country, 'tag': tag, 'government_rank': rank, 'reforms': set(active_reforms),
                'culture_group': culture_groups.get(country.get('primary_culture')),
                'religion_group': religion_groups.get(country.get('religion')),
                'is_elector': country.get('elector') == 'yes',
                'is_part_of_hre': provinces.get(capital, {}).get('hre') == 'yes',
                'is_emperor': tag == emperor, 'is_emperor_of_china': tag == celestial,
                'subject': subjects.get(tag), 'num_of_cities': cities[tag], 'total_development': development[tag],
                'capital_scope': {'area': area, 'region': region, 'superregion': superregions.get(region),
                                  'continent': continents.get(capital)},
                'attributes': {key for reform in active_reforms for key, value in assignments(
                    reforms.get(reform, {}).get('custom_attributes', [])) if value == 'yes'},
            }

    @staticmethod
    def _emperor_rank(roots):
        files = {path.name: path for path in layered_files(roots, 'common', extension='lua')}
        sources = [files['defines.lua'], *layered_files(roots, 'common/defines', extension='lua')]
        ranks = re.findall(r'\bHRE_RANK\s*=\s*([123])\b', '\n'.join(read_text(path) for path in sources))
        return ranks[-1] if ranks else None

    def _matches(self, block, state, operator='and'):
        disjunction = operator in {'or', 'nor', 'not'}
        uncertain = False
        for key, value in block:
            result = self._condition(key, value, state)
            if result is disjunction:
                return operator in {'or', 'nand'}
            uncertain |= result is None
        if uncertain:
            return None
        return operator in {'and', 'not', 'nor'}

    def _condition(self, key, value, state):
        if key in {'and', 'or', 'not', 'nor', 'nand'}:
            return self._matches(value, state, key)
        if key in self.scripted:
            result = self._matches(self.scripted[key], state)
            return None if result is None else result == (value == 'yes')
        if key == 'overlord':
            return bool(state['subject']) and self._matches(value, self.states[state['subject'][0]])
        if key == 'capital_scope':
            return self._matches(value, state['capital_scope'])
        if key in {'is_subject', 'is_march', 'is_subject_of_type'}:
            subject = state['subject']
            if key == 'is_subject_of_type':
                return bool(subject) and subject[1] == value
            result = bool(subject) if key == 'is_subject' else bool(subject) and subject[2] == 'march'
            return result == (value == 'yes')
        if key in {'has_reform', 'have_had_reform', 'has_country_flag', 'has_country_modifier',
                   'has_government_attribute'}:
            field = {'has_reform': 'reforms', 'have_had_reform': 'past_reforms', 'has_country_flag': 'flags',
                     'has_country_modifier': 'modifiers', 'has_government_attribute': 'attributes'}[key]
            return value in state.get(field, ())
        if key in {'has_ruler', 'has_heir', 'has_ruler_flag'}:
            person = dict(assignments(state.get('heir' if key == 'has_heir' else 'monarch', [])))
            if key == 'has_ruler_flag':
                return person.get('set_ruler_flag') == value
            return decode_localisation(person.get('name', '')) == decode_localisation(value)
        actual = state.get('primary_culture' if key == 'culture' else key)
        if actual is None:
            return None
        if key in {'government_rank', 'num_of_cities', 'total_development'}:
            return float(actual) >= float(value)
        return actual == (value == 'yes' if isinstance(actual, bool) else value)

    def _label_keys(self, state, rank):
        state = dict(state, government_rank=rank)
        ruler_rank = self.emperor_rank if state['is_emperor'] and self.emperor_rank else rank
        keys = {}
        for trigger, fields in self.rules:
            if self._matches(trigger, state) is not True:
                continue
            for field, ranks in fields.items():
                key = ranks.get(rank if field == 'rank' else ruler_rank)
                if key:
                    keys.setdefault(field, key)
            if len(keys) == len(GOVERNMENT_FIELDS):
                break
        return keys

    def apply(self, countries, strings, english_strings):
        for country in countries:
            state = self.states[country['tag']]
            keys = self._label_keys(state, state['government_rank'])
            fallback = GOVERNMENT_FALLBACK.get(state.get('government'), ('政体未记录', 'Government not recorded'))
            country['government'] = strings.get(keys.get('rank'), fallback[0])
            country['en']['government'] = english_strings.get(keys.get('rank'), fallback[1])
            if ruler := country.get('ruler'):
                title = keys.get('ruler_' + (ruler['gender'] or ''))
                ruler['title'] = strings.get(title, '')
                ruler['en']['title'] = english_strings.get(title, '')

    def add_backgrounds(self, countries, mod, english):
        texts = mod / 'localisation/NYDD_startscreen_l_english.yml'
        keys = {match[1] for line in read_text(texts).split('\n')
                if (match := LOC_LINE.match(line.rstrip('\r')))}
        strings = load_localisation((mod,), keys, texts.name, line_break='\n')
        english_strings = load_localisation((english,), keys, texts.name, line_break='\n')

        def paragraphs(description):
            lines = re.sub(r'@[A-Z0-9]{3}(?![A-Z0-9])', '', description).splitlines()
            return [line.strip() for line in lines if line.strip() not in {'', '背景', 'Background'}]

        path = mod / 'customizable_localization/00_start_screen.txt'
        choices = []
        for kind, block in entries(path):
            if kind != 'defined_text' or not isinstance(block, list):
                continue
            fields = list(assignments(block))
            if dict(fields).get('name') != 'StartScreenUpLeftDesc':
                continue
            for key, option in fields:
                if key != 'text' or not isinstance(option, list):
                    continue
                choice = dict(assignments(option))
                localisation_key = choice.get('localisation_key')
                if original := paragraphs(strings.get(localisation_key, '')):
                    translation = paragraphs(english_strings.get(localisation_key, ''))
                    choices.append((naming_trigger(choice.get('trigger', [])), original, translation))
            break
        for country in countries:
            state = self.states[country['tag']]
            for condition, original, translation in choices:
                if self._matches(condition, state) is True:
                    country['background'] = original
                    if translation:
                        country['en']['background'] = translation
                    break


SUBJECT_LABELS = {
    'vassal': ('附庸国', 'Vassal'),
    'dependency': ('附属国', 'Subject'),
    'union': ('被联统国', 'Junior Partner'),
    'march': ('卫戍国', 'March'),
    'tributary_state': ('朝贡国', 'Tributary State'),
}
SUBJECT_ICON_NAMES = {
    'GFX_diplomacy_leadvassal': 'subject',
    'GFX_diplomacy_amvassal': 'overlord',
    'GFX_icon_diplomacy_lead_appanage': 'appanage',
    'GFX_icon_diplomacy_am_appanage': 'appanage-overlord',
    'GFX_diplomacy_leadunions': 'union',
    'GFX_diplomacy_inunion': 'union-overlord',
    'GFX_diplomacy_leadmarch': 'march',
    'GFX_diplomacy_weakmarch': 'march-overlord',
    'GFX_diplomacy_leadtributary': 'tribute',
    'GFX_diplomacy_amtributary': 'tribute-overlord',
}


def build_subject_icons(roots, subtypes, panel_dir):
    types = dict(definitions(roots, 'common/subject_types'))
    directions = ('diplomacy_overlord_sprite', 'diplomacy_subject_sprite')
    default = types['default']
    default_sprites = tuple(default[field] for field in directions)
    pairs = {}
    for subtype in sorted(subtypes):
        fields, seen = {}, set()
        subject_type = 'personal_union' if subtype == 'union' else subtype
        while subject_type in types and subject_type not in seen:
            seen.add(subject_type)
            definition = types[subject_type]
            for field in directions:
                if field in definition:
                    fields.setdefault(field, definition[field])
            subject_type = definition.get('copy_from')
        pairs[subtype] = tuple(fields.get(field, default[field]) for field in directions)

    wanted = set(default_sprites)
    wanted.update(sprite for pair in pairs.values() for sprite in pair)
    textures = {}
    for path in layered_files(roots, 'interface', 'gfx'):
        for key, block in entries(path):
            if key.lower() != 'spritetypes' or not isinstance(block, list):
                continue
            for kind, sprite in assignments(block):
                if kind.lower() != 'spritetype' or not isinstance(sprite, list):
                    continue
                data = dict(assignments(sprite))
                name, texture = data.get('name'), data.get('texturefile')
                if name in wanted and isinstance(texture, str):
                    textures[name] = Path(texture)

    icons = {}
    for name, texture in textures.items():
        # Some vanilla entries still name a TGA whose installed texture is DDS.
        candidates = (texture, texture.with_suffix('.dds'))
        source = next((root / candidate for root in reversed(roots)
                       for candidate in candidates if (root / candidate).is_file()), None)
        if source is None:
            continue
        icon = SUBJECT_ICON_NAMES.get(name, 'subject-' + name.removeprefix('GFX_icon_').lower().replace('_', '-'))
        save_sprite(load_rgba(source), panel_dir / f'{icon}.webp')
        icons[name] = icon
    fallback = tuple(icons[name] for name in default_sprites)
    return {subtype: tuple(icons.get(sprite, default_icon) for sprite, default_icon in zip(pair, fallback))
            for subtype, pair in pairs.items()}


def ruler_details(history, translation):
    monarch = {key.lower(): value for key, value in assignments(history.get('monarch', []))}
    if not monarch:
        return None
    translated = {key.lower(): value for key, value in assignments(translation.get('monarch', []))}
    if monarch.get('birth_date') != translated.get('birth_date'):
        translated = {}

    def clean(fields, key):
        value = decode_localisation(fields.get(key, ''))
        return '' if re.search(r'[\x00-\x1f\x7f-\x9f\ufffd]', value) else value

    regency = monarch.get('regency') == 'yes'
    name, english_name = clean(monarch, 'name'), clean(translated, 'name')
    if not name and not english_name and not regency:
        return None
    birth = parse_date(monarch.get('birth_date'))
    dynasty = clean(monarch, 'dynasty')
    female = monarch.get('female')
    # Councils use regent = yes without a person's sex, birth date, or dynasty.
    collective_regency = monarch.get('regent') == 'yes' and female is None and birth is None and not dynasty
    gender = None if regency or collective_regency else ('female' if female == 'yes' else 'male')
    age = START[0] - birth[0] - (START[1:] < birth[1:]) if birth and birth <= START else None
    stats = {key: int(monarch[key]) for key in ('adm', 'dip', 'mil') if monarch.get(key, '').isdigit()}
    return dict(name=name or ('摄政会议' if regency else english_name),
                dynasty=dynasty,
                en=dict(name=english_name or ('Regency Council' if regency else name),
                        dynasty=clean(translated, 'dynasty').removeprefix('¿')),
                age=age, gender=gender, stats=stats)


def surname_first_cultures(roots):
    cultures = {'bai', 'miao', 'zhuang', 'manchu', 'vietnamese'}
    for path in layered_files(roots, 'common/cultures'):
        for group, block in entries(path):
            if group in {'east_asian', 'japanese_g', 'korean_g'} and isinstance(block, list):
                cultures.update(key for key, value in assignments(block) if isinstance(value, list))
    return cultures


def ruler_full_name(name, dynasty, surname_first, compact=False):
    name, dynasty = name.strip(), dynasty.strip()
    if not name or not dynasty:
        return name or dynasty
    folded_name, folded_dynasty = name.casefold(), dynasty.casefold()
    if (folded_name == folded_dynasty or folded_name.startswith(folded_dynasty + ' ')
            or folded_name.endswith(' ' + folded_dynasty)):
        return name

    if (name, dynasty) == ('「世宗」 祹', '李'):
        return '李祹「世宗」'
    if (name, dynasty) == ('Sejong Do', 'Yi'):
        return 'Yi Do (Sejong)'
    if dynasty in {'天皇', 'Tenno'}:
        return name  # This is supplied as the ruler's title by the naming rule.
    if surname_first:
        if compact:
            return ''.join((dynasty + name).split())
        return f'{dynasty} {name}'

    epithet = re.fullmatch(r"(.+?)\s+([「『“\"'].+)", name)
    if epithet:
        return f'{epithet[1]} {dynasty} {epithet[2]}'
    return f'{name} {dynasty}'


def add_ruler_full_names(ruler, culture, surname_first):
    if not ruler:
        return
    family_name_first = culture in surname_first or ruler['dynasty'].startswith('¿')
    ruler['dynasty'] = ruler['dynasty'].removeprefix('¿')
    ruler['fullName'] = ruler_full_name(ruler['name'], ruler['dynasty'], family_name_first,
                                       compact=family_name_first)
    english = ruler['en']
    if english['dynasty'] or english['name'] != ruler['name']:
        english['fullName'] = ruler_full_name(english['name'], english['dynasty'] or ruler['dynasty'],
                                             family_name_first)
    else:
        english['fullName'] = ruler['fullName']


def religion_frames(roots):
    frames = {}
    for file in layered_files(roots, 'common/religions'):
        for _, group in entries(file):
            if not isinstance(group, list):
                continue
            for religion, definition in assignments(group):
                if isinstance(definition, list):
                    icon = dict(assignments(definition)).get('icon', '')
                    if isinstance(icon, str) and icon.isdigit():
                        frames[religion] = int(icon)
    return frames


def add_dossiers(countries, roots, histories, values, locales, relations, subject_icons, english):
    strings, english_strings = locales
    countries_by_tag = {country['tag']: country for country in countries}
    recommendations = recommended_tags(roots[-1])
    contacts = defaultdict(lambda: defaultdict(dict))
    statuses = {}

    def connect(first, second, icon, labels, details=('', '')):
        contacts[first][(icon, *labels)][second] = details

    wars, truces = war_records(roots, set(countries_by_tag))
    for (first, second), names in wars.items():
        detail = '、'.join(sorted(names))
        detail_en = ', '.join(sorted(names))
        connect(first, second, 'war', ('战争', 'War'), (detail, detail_en))
        connect(second, first, 'war', ('战争', 'War'), (detail, detail_en))
    for (first, second), until in truces.items():
        date = '.'.join(map(str, until))
        details = ('按历史战事推算至 ' + date, 'Estimated until ' + date)
        connect(first, second, 'truce', ('停战', 'Truce'), details)
        connect(second, first, 'truce', ('停战', 'Truce'), details)

    for kind, first, second, subtype in relations:
        if first not in countries_by_tag or second not in countries_by_tag:
            continue
        if kind in ('alliance', 'royal_marriage'):
            icon, labels = ('alliance', ('联盟', 'Alliance')) if kind == 'alliance' else ('marriage', ('联姻', 'Royal Marriage'))
            connect(first, second, icon, labels)
            connect(second, first, icon, labels)
        elif kind == 'guarantee':
            connect(first, second, 'guarantee', ('保障独立', 'Guaranteeing Independence'))
            connect(second, first, 'guaranteed', ('独立受保障', 'Independence Guaranteed'))
        elif kind in ('vassal', 'union', 'march', 'dependency'):
            labels = SUBJECT_LABELS.get(subtype)
            if not labels:
                zh, en = SUBJECT_LABELS[kind]
                labels = strings.get(subtype, zh), english_strings.get(subtype, en)
            icon, reverse = subject_icons[subtype]
            connect(first, second, icon, labels)
            connect(second, first, reverse, ('宗主国', 'Overlord'), labels)
            statuses[second] = dict(tag=first, label=labels[0], en=labels[1])

    vanilla_history = history_files((roots[0],), 'countries', r'^([A-Z0-9]{3})\s*-')
    english_history = history_files((english,), 'countries', r'^([A-Z0-9]{3})\s*-')
    surname_first = surname_first_cultures(roots)
    for country in countries:
        tag = country['tag']
        history = values[tag]
        country['overlord'] = statuses.get(tag)
        path = histories.get(tag)
        translated_path = english_history.get(tag)
        if translated_path is None and path and not path.is_relative_to(roots[-1]):
            translated_path = vanilla_history.get(tag)
        translation = snapshot(translated_path, (), ('monarch',)) if translated_path else {}
        country['ruler'] = ruler_details(history, translation)
        add_ruler_full_names(country['ruler'], history.get('primary_culture'), surname_first)
        country['recommended'] = tag in recommendations
        country['diplomacy'] = [
            dict(icon=icon, label=label, en=label_en,
                 contacts=[dict(tag=other, detail=detail, en=detail_en)
                           for other, (detail, detail_en) in group.items()])
            for (icon, label, label_en), group in contacts[tag].items()
        ]


def pack_colors(pixels):
    rgb = pixels.astype(np.uint32)
    return (rgb[..., 0] << 16) | (rgb[..., 1] << 8) | rgb[..., 2]


def trace_polygons(mask, x, y):
    """Trace pixel edges so neighbors start with exactly the same boundary."""
    horizontal = np.argwhere(np.diff(np.pad(mask, ((1, 1), (0, 0))), axis=0))[:, ::-1]
    vertical = np.argwhere(np.diff(np.pad(mask, ((0, 0), (1, 1))), axis=1))[:, ::-1]
    segments = np.concatenate([
        np.stack((horizontal, horizontal + [1, 0]), axis=1),
        np.stack((vertical, vertical + [0, 1]), axis=1),
    ])
    faces = shapely.get_parts(shapely.polygonize(shapely.linestrings(segments)))
    # Polygonize also returns the faces enclosed by holes. Keep only this label.
    points = shapely.get_coordinates(shapely.point_on_surface(faces)).astype(int)
    inside = mask[points[:, 1], points[:, 0]]
    return shapely.affinity.translate(MultiPolygon(faces[inside]), xoff=x, yoff=y)


def boundary_rings(geometry):
    for polygon in shapely.get_parts(geometry):
        for ring in (polygon.exterior, *polygon.interiors):
            yield [(int(x), int(y)) for x, y in list(ring.coords)[:-1]]


def border_junctions(geometry):
    neighbors = {}
    for shape in geometry:
        for ring in boundary_rings(shape):
            for i, point in enumerate(ring):
                neighbors.setdefault(point, set()).update((ring[i - 1], ring[(i + 1) % len(ring)]))
    return {point for point, edges in neighbors.items() if len(edges) > 2}


def rounded_corner(before, point, after, radius):
    x, y = point
    ax, ay = before[0] - x, before[1] - y
    bx, by = after[0] - x, after[1] - y
    if not radius or ax * by == ay * bx:
        end = f'{x},{y}'
        return 'L' + end, end
    a, b = math.hypot(ax, ay), math.hypot(bx, by)
    trim = min(radius, a / 2, b / 2)
    end = f'{x + bx * trim / b:.1f},{y + by * trim / b:.1f}'
    return f'L{x + ax * trim / a:.1f},{y + ay * trim / a:.1f}Q{x},{y} {end}', end


def closed_path(ring, junctions, radius):
    corners = [
        rounded_corner(ring[i - 1], point, ring[(i + 1) % len(ring)], 0 if point in junctions else radius)
        for i, point in enumerate(ring)
    ]
    # Both sides of a shared edge use the same trim and fixed junctions.
    return 'M' + corners[0][1] + ''.join(command for command, _ in corners[1:]) + corners[0][0] + 'Z'


def outline_path(geometry, junctions):
    return ''.join(closed_path(ring, junctions, 2.5) for ring in boundary_rings(geometry))


def province_paths(provinces, country_map, water):
    """Trace only interior boundaries. National borders and coasts already have paths."""
    horizontal = ((provinces[1:, :] != provinces[:-1, :])
                  & (country_map[1:, :] == country_map[:-1, :])
                  & ~water[1:, :] & ~water[:-1, :])
    vertical = ((provinces[:, 1:] != provinces[:, :-1])
                & (country_map[:, 1:] == country_map[:, :-1])
                & ~water[:, 1:] & ~water[:, :-1])
    hy, hx = np.nonzero(horizontal)
    vy, vx = np.nonzero(vertical)
    labels = np.concatenate([country_map[hy, hx], country_map[vy, vx]])
    if not labels.size:
        return {}
    h_start, v_start = np.column_stack((hx, hy + 1)), np.column_stack((vx + 1, vy))
    edges = np.concatenate([
        np.stack((h_start, h_start + [1, 0]), axis=1),
        np.stack((v_start, v_start + [0, 1]), axis=1),
    ])
    order = np.argsort(labels, kind='stable')
    ordered_labels = labels[order]
    groups = np.r_[0, np.flatnonzero(np.diff(ordered_labels)) + 1, labels.size]
    padded_owners = np.pad(np.where(water, -1, country_map.astype(np.int16)), 1, constant_values=-1)
    paths = {}

    for start, stop in zip(groups[:-1], groups[1:]):
        owner = int(ordered_labels[start])
        segments = shapely.linestrings(edges[order[start:stop]])
        merged = shapely.line_merge(shapely.multilinestrings(segments))
        lines = shapely.get_parts(shapely.simplify(merged, tolerance=.8))
        paths[owner] = ''.join(province_line(line, padded_owners, owner) for line in lines)
    return paths


def province_line(line, padded_owners, owner):
    points = [(int(x), int(y)) for x, y in line.coords]
    if points[0] == points[-1]:
        return closed_path(points[:-1], set(), 1.0)

    def endpoint(point, adjacent):
        x, y = point
        if np.all(padded_owners[y:y + 2, x:x + 2] == owner):
            return f'{x},{y}'
        # Let the line reach through the rounded outer edge. The renderer
        # clips it to this country's territory.
        dx, dy = x - adjacent[0], y - adjacent[1]
        length = math.hypot(dx, dy)
        return f'{x + dx * 3.5 / length:.1f},{y + dy * 3.5 / length:.1f}'

    inside = ''.join(rounded_corner(points[i - 1], points[i], points[i + 1], 1.0)[0]
                     for i in range(1, len(points) - 1))
    return 'M' + endpoint(points[0], points[1]) + inside + 'L' + endpoint(points[-1], points[-2])


def build_relief(vanilla, output, water):
    with Image.open(vanilla / 'map/heightmap.bmp') as image:
        elevation = np.array(image.convert('L'), dtype=np.float32)
    if elevation.shape != water.shape:
        raise ValueError('The elevation and province maps use different dimensions.')
    elevation = cv2.GaussianBlur(elevation, (0, 0), 0.9)
    dx = cv2.Sobel(elevation, cv2.CV_32F, 1, 0, scale=0.125)
    dy = cv2.Sobel(elevation, cv2.CV_32F, 0, 1, scale=0.125)
    shade = np.clip((dx * .7 + dy * .7) / 10, -1, 1)
    pixels = np.zeros((*water.shape, 4), dtype=np.uint8)
    pixels[..., :3] = np.where((shade > 0)[..., None], [246, 224, 186], [19, 26, 27])
    pixels[..., 3] = np.rint(np.abs(shade) * 12).astype(np.uint8) * 4
    pixels[water, 3] = 0
    Image.fromarray(pixels).save(output / 'relief.webp', lossless=True)


def build_map(vanilla, output, countries, owner_for_province):
    with (vanilla / 'map/definition.csv').open(encoding='cp1252', newline='', errors='replace') as source:
        rows = [row for row in csv.reader(source, delimiter=';') if row and row[0].isdigit()]
    colors = np.array([row[1:4] for row in rows], dtype=np.uint8)
    province_ids = np.array([row[0] for row in rows], dtype=np.uint16)
    palette = np.zeros(1 << 24, dtype=np.uint16)
    palette[pack_colors(colors)] = province_ids
    with Image.open(vanilla / 'map/provinces.bmp') as image:
        provinces = palette[pack_colors(np.asarray(image.convert('RGB')))]

    world = dict(entries(vanilla / 'map/default.map'))
    water_ids = [int(i) for key in ('sea_starts', 'lakes') for i in world[key] if i.isdigit()]
    water = np.isin(provinces, water_ids)
    height, width = provinces.shape
    tags = {country['tag']: i for i, country in enumerate(countries, 1)}
    owners = np.zeros(int(province_ids.max()) + 1, dtype=np.uint16)
    for province in province_ids:
        owners[province] = tags.get(owner_for_province.get(int(province)), 0)
    country_map = owners[provinces]
    country_map[water] = 0
    present = np.bincount(country_map.ravel(), minlength=len(countries) + 1)

    province_borders = province_paths(provinces, country_map, water)
    geometry = [trace_polygons((country_map == 0) & ~water, 0, 0)]
    metadata = []
    for idx, country in enumerate(countries, 1):
        if not present[idx]:
            continue
        x, y, w, h = cv2.boundingRect(np.uint8(country_map == idx))
        mask = country_map[y:y + h, x:x + w] == idx
        # A point inside the widest part of the territory is a useful label anchor.
        distance = cv2.distanceTransform(np.uint8(np.pad(mask, 1)), cv2.DIST_L2, 3)
        _, radius, _, (px, py) = cv2.minMaxLoc(distance)
        geometry.append(trace_polygons(mask, x, y))
        country.update(center=[x + px - .5, y + py - .5], bounds=[x, y, w, h],
                       radius=round(radius, 1), area=int(present[idx]))
        if idx in province_borders:
            country['provinces'] = province_borders[idx]
        metadata.append(country)

    expected_area = np.count_nonzero(~water)
    if sum(shape.area for shape in geometry) != expected_area or not shapely.coverage_is_valid(geometry):
        raise ValueError('The traced polygons do not cover the land and borders exactly.')
    simplified = shapely.coverage_simplify(geometry, tolerance=1.65)
    if not shapely.coverage_is_valid(simplified):
        raise ValueError('The simplified boundaries no longer form a valid coverage.')
    junctions = border_junctions(simplified)
    for country, shape in zip(metadata, simplified[1:]):
        country['outline'] = outline_path(shape, junctions)
    coastline = shapely.make_valid(shapely.coverage_union_all(simplified), method='structure', keep_collapsed=False)
    land = outline_path(coastline, junctions)
    build_relief(vanilla, output, water)
    return width, height, land, province_borders.get(0, ''), metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--steamapps', type=Path, default=DEFAULT_STEAMAPPS)
    parser.add_argument('--mod', type=Path, help='Override the DD Workshop directory.')
    parser.add_argument('--english-mod', type=Path, help='Override the DD English translation directory.')
    args = parser.parse_args()
    vanilla = args.steamapps / 'common/Europa Universalis IV'
    workshop = args.steamapps / 'workshop/content/236850'
    mod = args.mod or workshop / '3416098047'
    english = args.english_mod or workshop / '3421928658'
    if args.english_mod and not (english / 'localisation').is_dir():
        parser.error(f'English localisation directory not found in {english}')
    chinese = workshop / '2976470733'
    supplementary = workshop / '1999055990'
    output = Path(__file__).resolve().parents[1] / 'dist/data'
    (output / 'flags').mkdir(parents=True, exist_ok=True)

    roots = (vanilla, supplementary, mod)
    provinces = province_snapshot(roots)
    owner_for_province = {int(i): province.get('owner') for i, province in provinces.items()}
    owner_tags = sorted(set(owner_for_province.values()) - {None, '---', 'XXX', 'NONE'})
    histories = history_files(roots, 'countries', r'^([A-Z0-9]{3})\s*-')
    tag_files = {}
    for root in roots:
        for file in sorted((root / 'common/country_tags').glob('*.txt')):
            tag_files.update(dict(entries(file)))

    country_values = {tag: country_snapshot(histories[tag]) if tag in histories else {} for tag in owner_tags}
    relations = list(diplomacy_records(roots))
    government_names = GovernmentNames(roots, histories, country_values, provinces, relations)
    religion_icons = religion_frames(roots)
    keys = set(owner_tags)
    keys.update(subtype for _, _, _, subtype in relations)
    for values in country_values.values():
        keys.add(f'PROV{values.get("capital", "")}')
        keys.update(values[key] for key in ('religion', 'primary_culture') if key in values)
    keys.update(government_names.localisation_keys)
    base_strings = load_localisation((vanilla, chinese, supplementary), keys)
    mod_strings = load_localisation((mod,), keys)
    strings = base_strings | mod_strings
    english_strings = load_localisation((vanilla,), keys)
    for key, value in mod_strings.items():
        if value != base_strings.get(key):
            english_strings[key] = value
    english_strings.update(load_localisation((english,), keys))

    countries = []
    for tag in owner_tags:
        values = country_values[tag]
        color = [122, 111, 90]
        if isinstance(tag_files.get(tag), str):
            for root in reversed(roots):
                definition = root / 'common' / tag_files[tag]
                if definition.is_file():
                    data = dict(entries(definition)).get('color', [])
                    if len(data) == 3 and all(item.isdigit() for item in data):
                        color = [max(0, min(255, int(item))) for item in data]
                    break
        country = dict(tag=tag, name=strings.get(tag, tag), color=color,
                       en=dict(name=english_strings.get(tag, strings.get(tag, tag))))
        capital = values.get('capital')
        country['capital'] = strings.get(f'PROV{capital}')
        country['en']['capital'] = english_strings.get(f'PROV{capital}')
        for field, key in (('religion', 'religion'), ('culture', 'primary_culture')):
            value = values.get(key, '')
            fallback = value.replace('_', ' ').title()
            country[field] = strings.get(value, fallback) or None
            country['en'][field] = english_strings.get(value, fallback) or None
        country['religionIcon'] = religion_icons.get(values.get('religion'))
        countries.append(country)

    width, height, land, neutral_provinces, countries = build_map(vanilla, output, countries, owner_for_province)
    build_panel_assets(vanilla, mod, output)
    map_tags = {country['tag'] for country in countries}
    subtypes = {subtype for kind, first, second, subtype in relations
                if kind in {'vassal', 'union', 'march', 'dependency'} and first in map_tags and second in map_tags}
    subject_icons = build_subject_icons(roots, subtypes, output / 'panel')
    add_dossiers(countries, roots, histories, country_values, (strings, english_strings), relations, subject_icons, english)
    government_names.apply(countries, strings, english_strings)
    government_names.add_backgrounds(countries, mod, english)
    build_shields(vanilla, mod, output, countries)

    data = dict(width=width, height=height, land=land, neutralProvinces=neutral_provinces, countries=countries)
    payload = json.dumps(data, ensure_ascii=False, separators=(',', ':'))
    build_panel_fonts(vanilla, chinese, output / 'panel', panel_characters(output, payload))
    (output / 'atlas.json').write_text(payload, encoding='utf-8')
    print(f'Atlas built with {len(countries)} countries, {width} × {height} pixels.')


if __name__ == '__main__':
    main()
