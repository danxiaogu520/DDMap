"""Extract the EU4 lobby artwork and composite the game's shield sprites."""

from PIL import Image


ICONS = {
    'yes': 'icon_trigger_yes.dds',
    'no': 'icon_trigger_no.dds',
    'war': 'icon_diplomacy_war.dds',
    'truce': 'icon_truce.dds',
    'alliance': 'icon_alliance.dds',
    'marriage': 'icon_diplomacy_royalmarriage.dds',
    'guarantee': 'icon_diplomacy_guaranting.dds',
    'guaranteed': 'icon_diplomacy_guaranteed.dds',
    'subject': 'icon_diplomacy_leadvassal.dds',
    'overlord': 'icon_diplomacy_amvassal.dds',
    'appanage': 'icon_diplomacy_lead_appanage.dds',
    'appanage-overlord': 'icon_diplomacy_am_appanage.dds',
    'union': 'icon_diplomacy_leadunions.dds',
    'union-overlord': 'icon_diplomacy_inunion.dds',
    'march': 'icon_leadmarch.dds',
    'march-overlord': 'icon_weakmarch.dds',
    'tribute': 'icon_diplomacy_leadtributary.dds',
    'tribute-overlord': 'icon_diplomacy_amtributary.dds',
    'capital': 'current_capital.dds',
    'culture': 'monarch_cultures_icon.dds',
    'ruler': 'monarch_heir_crown_icon.dds',
    'adm': 'icon_powers_administrative.dds',
    'dip': 'icon_powers_diplomatic.dds',
    'mil': 'icon_powers_military.dds',
}

def load_rgba(path):
    with Image.open(path) as source:
        return source.convert('RGBA')


def save_sprite(image, output):
    image.save(output, lossless=True, method=4)


def build_background_reader_assets(vanilla, panel_dir):
    interface = vanilla / 'gfx/interface'
    button = load_rgba(interface / 'standard_button_224.dds')
    save_sprite(button.crop(button.getbbox()), panel_dir / 'reader-button.webp')
    frame = load_rgba(interface / 'tiles_dialog.dds')
    save_sprite(frame, panel_dir / 'reader-frame.webp')
    close = load_rgba(interface / 'closebutton.dds')
    save_sprite(close.crop(close.getbbox()), panel_dir / 'reader-close.webp')


def build_panel_assets(vanilla, mod, output):
    interface = vanilla / 'gfx/interface'
    panel_dir = output / 'panel'
    panel_dir.mkdir(exist_ok=True)
    build_background_reader_assets(vanilla, panel_dir)
    lobby = load_rgba(interface / 'lobby_right_bg.dds')
    save_sprite(lobby.crop((25, 58, 247, 139)), panel_dir / 'fabric.webp')
    for name, file in ICONS.items():
        save_sprite(load_rgba(interface / file), panel_dir / f'{name}.webp')
    gender = load_rgba(interface / 'gender_icon.dds')
    for index, name in enumerate(('female', 'male')):
        save_sprite(gender.crop((16 * index, 0, 16 * (index + 1), 16)), panel_dir / f'{name}.webp')
    religion_strip = load_rgba(mod / 'gfx/interface/icon_religion_small.dds')
    religion_dir = panel_dir / 'religions'
    religion_dir.mkdir(exist_ok=True)
    size = religion_strip.height
    for index in range(religion_strip.width // size):
        icon = religion_strip.crop((index * size, 0, (index + 1) * size, size))
        save_sprite(icon, religion_dir / f'{index + 1}.webp')


def build_shields(vanilla, mod, output, countries):
    interface = vanilla / 'gfx/interface'
    flag_dir = output / 'flags'
    mini_dir = flag_dir / 'mini'
    mini_dir.mkdir(parents=True, exist_ok=True)
    shapes = [
        (load_rgba(interface / 'shield_frame.dds'), load_rgba(interface / 'shield_mask.tga'), (15, 16), flag_dir),
        (load_rgba(interface / 'small_shield_overlay.dds'), load_rgba(interface / 'small_shield_mask.tga'), (4, 5), mini_dir),
    ]
    for country in countries:
        tag = country['tag']
        for root in (mod, vanilla):
            path = root / 'gfx/flags' / f'{tag}.tga'
            if not path.is_file():
                continue
            flag = load_rgba(path)
            for frame, mask, position, directory in shapes:
                badge = flag.resize(mask.size, Image.Resampling.LANCZOS)
                badge.putalpha(mask.getchannel('A'))
                result = Image.new('RGBA', frame.size)
                result.alpha_composite(badge, dest=position)
                result.alpha_composite(frame)
                save_sprite(result, directory / f'{tag}.webp')
            country['flag'] = True
            break
