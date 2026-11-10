"""Convert the visible glyphs in EU4's bitmap UI fonts to subset web fonts."""

import re

import cv2
import numpy as np
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from PIL import Image


UNITS = 1024
UPSAMPLE = 4
ROMAN_NUMERALS = {'Ⅰ': 'I', 'Ⅹ': 'X'}
ACCENTED_LETTERS = {
    'ć': ('c', '´'),
    'ń': ('n', '´'),
    'ŵ': ('w', '^'),
    'ł': ('l', '/'),
}


def metrics(line):
    return {name: int(value) for name, value in re.findall(r'(\w+)\s*=\s*(-?\d+)', line)}


def trace_glyph(atlas, record, scale, baseline):
    pen = TTGlyphPen(None)
    left = 0
    x, y = record['x'], record['y']
    w, h = record['width'], record['height']
    if w and h:
        bitmap = np.pad(atlas[y:y + h, x:x + w], 1)
        enlarged = cv2.resize(bitmap, None, fx=UPSAMPLE, fy=UPSAMPLE, interpolation=cv2.INTER_LINEAR)
        contours, _ = cv2.findContours(np.uint8(enlarged >= 112), cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        left_edges = []
        for contour in contours:
            coords = cv2.approxPolyDP(contour, .4, True).reshape(-1, 2)
            if len(coords) < 3:
                continue
            points = [
                (round((record['xoffset'] + (px + .5) / UPSAMPLE - 1) * scale),
                 round((baseline - record['yoffset'] - (py + .5) / UPSAMPLE + 1) * scale))
                for px, py in coords
            ]
            pen.moveTo(points[0])
            for point in points[1:]:
                pen.lineTo(point)
            pen.closePath()
            left_edges.append(min(x for x, _ in points))
        left = min(left_edges, default=0)
    return pen.glyph(), (max(0, round(record['xadvance'] * scale)), left)


def compose_letter(base, accent, stroke, glyphs, widths):
    letter, mark = glyphs[base], glyphs[accent]
    letter.recalcBounds(None)
    mark.recalcBounds(None)
    sx, sy = (.75, .4) if stroke else (1, .7)
    dx = round((letter.xMin + letter.xMax - sx * (mark.xMin + mark.xMax)) / 2)
    if stroke:
        dy = round((letter.yMin + letter.yMax - sy * (mark.yMin + mark.yMax)) / 2)
    else:
        dy = round(letter.yMax + UNITS * .035 - sy * mark.yMin)
    pen = TTGlyphPen(glyphs)
    pen.addComponent(base, (1, 0, 0, 1, 0, 0))
    pen.addComponent(accent, (sx, 0, 0, sy, dx, dy))
    return pen.glyph(), (widths[base][0], min(letter.xMin, round(dx + sx * mark.xMin)))


def build_font(definition, image, family, characters, output, ansi=False):
    lines = definition.read_text(encoding='utf-8-sig').splitlines()
    settings = metrics(lines[0])
    size = abs(settings['size'])
    common = metrics(lines[1])
    baseline = common['base']
    scale = UNITS / size
    records = {}
    for line in lines:
        if not line.startswith('char '):
            continue
        record = metrics(line)
        code = record['id']
        if ansi and 128 <= code < 256:
            mapped = bytes([code]).decode('cp1252', errors='ignore')
            if not mapped:
                continue
            code = ord(mapped)
        records[code] = record

    with Image.open(image) as source:
        atlas = np.array(source.convert('RGBA').getchannel('A'))
    glyphs = {'.notdef': TTGlyphPen(None).glyph()}
    widths = {'.notdef': (UNITS // 2, 0)}
    cmap = {}
    characters = set(characters)
    if ansi:
        for letter, parts in ACCENTED_LETTERS.items():
            if letter in characters:
                characters.update(parts)
    for code in sorted(ord(ch) for ch in characters if ch.isprintable()):
        record = records.get(code)
        if record is None and not ansi and 0x100 < code < 0xA00:
            record = records.get(code + 0xE000)
        if record is None and ansi and chr(code) in ROMAN_NUMERALS:
            record = records.get(ord(ROMAN_NUMERALS[chr(code)]))
        if record is not None:
            name = f'uni{code:04X}'
            cmap[code] = name
            glyphs[name], widths[name] = trace_glyph(atlas, record, scale, baseline)
    # The original Latin font lacks a few letters used in the map.
    # Draw them from the game's own letter and mark shapes.
    if ansi:
        for letter, (base, mark) in ACCENTED_LETTERS.items():
            code = ord(letter)
            if letter not in characters or code in cmap:
                continue
            name = f'uni{code:04X}'
            glyphs[name], widths[name] = compose_letter(
                cmap[ord(base)], cmap[ord(mark)], letter == 'ł', glyphs, widths
            )
            cmap[code] = name

    ascent = round(baseline * scale)
    descent = ascent - round(common['lineHeight'] * scale)
    font = FontBuilder(UNITS, isTTF=True)
    font.setupGlyphOrder(list(glyphs))
    font.setupCharacterMap(cmap)
    font.setupGlyf(glyphs)
    font.setupHorizontalMetrics(widths)
    font.setupHorizontalHeader(ascent=ascent, descent=descent)
    font.setupNameTable({'familyName': family, 'styleName': 'Regular',
                         'fullName': family, 'psName': family.replace(' ', '')})
    font.setupOS2(sTypoAscender=ascent, sTypoDescender=descent,
                  usWinAscent=max(ascent, max(getattr(glyph, 'yMax', 0) for glyph in glyphs.values())),
                  usWinDescent=max(-descent, -min(getattr(glyph, 'yMin', 0) for glyph in glyphs.values())))
    font.setupPost()
    font.font.flavor = 'woff'
    font.save(output)


def panel_characters(data, atlas_text):
    texts = [atlas_text]
    for file in (data.parent / 'index.html', data.parent / 'atlas.js'):
        texts.append(file.read_text(encoding='utf-8-sig'))
    return set(''.join(texts))


def build_panel_fonts(vanilla, chinese, output, characters):
    zh = chinese / 'gfx/fonts'
    en = vanilla / 'gfx/fonts'
    build_font(zh / 'zh-hans-24.fnt', zh / 'zh-hans-24.dds',
               'DD Panel Chinese', characters, output / 'panel-zh.woff')
    build_font(en / 'vic_22.fnt', en / 'vic_22.tga',
               'DD Panel English', characters, output / 'panel-en.woff', ansi=True)
