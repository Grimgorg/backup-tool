"""Draws the Vault Sync icon and writes it in every format the app and the builds need:

  vaultsync/assets/icon.png    1024 px, the source image (also used for the README)
  vaultsync/assets/window.png  256 px, the window and taskbar icon set by the app at runtime (all OSes)
  vaultsync/assets/icon.ico    Windows executable icon (16 to 256 px)
  vaultsync/assets/icon.icns   macOS app bundle icon (16 to 1024 px)

The motif: a vault door (round door with a handle wheel) inside two sync arrows, on a rounded square. It is drawn
four times larger than needed and scaled down, which smooths the edges. Run after changing the drawing:

  python make_icon.py          (needs Pillow: python -m pip install pillow)

The generated files are committed, so building the app does not need Pillow.
"""
import math
import os

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(ROOT, 'vaultsync', 'assets')
SIZE = 1024
SCALE = 4  # draw at 4x and scale down for smooth edges

TOP, BOTTOM = (29, 58, 138), (14, 116, 144)  # background gradient: deep blue to teal
ARROWS = (255, 255, 255)
DOOR, DOOR_RIM = (241, 245, 249), (148, 163, 184)
HANDLE = (30, 58, 138)


def draw(size):
    s = size * SCALE
    c = s / 2
    image = Image.new('RGBA', (s, s), (0, 0, 0, 0))

    # Rounded square with a vertical gradient.
    gradient = Image.new('RGBA', (s, s))
    pixels = ImageDraw.Draw(gradient)
    for y in range(s):
        t = y / (s - 1)
        pixels.line([(0, y), (s, y)], fill=tuple(round(a + (b - a) * t) for a, b in zip(TOP, BOTTOM)) + (255,))
    mask = Image.new('L', (s, s), 0)
    # About the padding of other macOS app icons, so it doesn't look bigger than them in the Dock.
    margin = 0.08 * s
    ImageDraw.Draw(mask).rounded_rectangle([margin, margin, s - margin, s - margin], radius=0.2 * s, fill=255)
    image.paste(gradient, (0, 0), mask)
    d = ImageDraw.Draw(image)

    # Two sync arrows going round clockwise, each an arc with an arrowhead at its end.
    radius, width = 0.315 * s, 0.07 * s
    box = [c - radius - width / 2, c - radius - width / 2, c + radius + width / 2, c + radius + width / 2]
    for start, end in ((200, 330), (20, 150)):
        d.arc(box, start, end, fill=ARROWS, width=round(width))
        angle = math.radians(end)
        px, py = c + radius * math.cos(angle), c + radius * math.sin(angle)
        nx, ny = math.cos(angle), math.sin(angle)  # outwards
        tx, ty = -math.sin(angle), math.cos(angle)  # direction of travel (clockwise on screen)
        half, length = width * 1.15, width * 1.45
        d.polygon([(px + nx * half, py + ny * half), (px - nx * half, py - ny * half),
                   (px + tx * length, py + ty * length)], fill=ARROWS)

    # Vault door: a disc with a rim, and a handle wheel with three spokes.
    door, rim = 0.2 * s, 0.028 * s
    d.ellipse([c - door, c - door, c + door, c + door], fill=DOOR_RIM)
    d.ellipse([c - door + rim, c - door + rim, c + door - rim, c + door - rim], fill=DOOR)
    bolt, ring = 0.012 * s, door - rim * 0.5
    for i in range(8):
        a = math.radians(22.5 + 45 * i)
        x, y = c + ring * math.cos(a), c + ring * math.sin(a)
        d.ellipse([x - bolt, y - bolt, x + bolt, y + bolt], fill=DOOR)
    spoke, knob, hub = 0.122 * s, 0.032 * s, 0.05 * s
    for degrees in (-90, 30, 150):
        a = math.radians(degrees)
        x, y = c + spoke * math.cos(a), c + spoke * math.sin(a)
        d.line([(c, c), (x, y)], fill=HANDLE, width=round(0.034 * s))
        d.ellipse([x - knob, y - knob, x + knob, y + knob], fill=HANDLE)
    d.ellipse([c - hub, c - hub, c + hub, c + hub], fill=HANDLE)
    d.ellipse([c - hub * 0.4, c - hub * 0.4, c + hub * 0.4, c + hub * 0.4], fill=DOOR_RIM)

    return image.resize((size, size), Image.LANCZOS)


def main():
    os.makedirs(ASSETS, exist_ok=True)
    big = draw(SIZE)
    big.save(os.path.join(ASSETS, 'icon.png'), optimize=True)
    draw(256).save(os.path.join(ASSETS, 'window.png'), optimize=True)
    big.save(os.path.join(ASSETS, 'icon.ico'), sizes=[(n, n) for n in (16, 24, 32, 48, 64, 128, 256)])
    big.save(os.path.join(ASSETS, 'icon.icns'))
    for name in ('icon.png', 'window.png', 'icon.ico', 'icon.icns'):
        print('{:12} {:>7} bytes'.format(name, os.path.getsize(os.path.join(ASSETS, name))))


if __name__ == '__main__':
    main()
