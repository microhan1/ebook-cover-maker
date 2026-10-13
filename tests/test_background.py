"""Background image handling: every common format and a pile of broken inputs.
The user's image must never be modified."""
from __future__ import annotations

import io
import os
import stat
import tempfile
import threading
import time
import unittest

import helpers  # noqa: F401  (must come first)
from PIL import Image, ImageChops

import render
from helpers import make_photo, sha
from render import Options


def cover(path: str, **kw) -> render.RenderResult:
    base = dict(title="제목", author="저자", publisher="유페이퍼", bg_image=path, template=4)
    base.update(kw)
    return render.render(Options(**base))


class Formats(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def path(self, name: str) -> str:
        return os.path.join(self.tmp.name, name)

    def _ok(self, p: str, **kw):
        before = sha(p)
        r = cover(p, **kw)
        self.assertEqual(r.image.size, (800, 1200))
        self.assertEqual(r.image.mode, "RGB")
        self.assertEqual(sha(p), before, "the user's image was modified")
        return r

    def test_common_formats_and_modes(self):
        src = Image.effect_noise((900, 700), 60).convert("RGB")
        made = {
            "rgb.jpg": ("JPEG", src),
            "gray.jpg": ("JPEG", src.convert("L")),
            "cmyk.jpg": ("JPEG", src.convert("CMYK")),
            "rgb.png": ("PNG", src),
            "gray.png": ("PNG", src.convert("L")),
            "pal.png": ("PNG", src.convert("P", palette=Image.Palette.ADAPTIVE)),
            "bw.png": ("PNG", src.convert("1")),
            "rgba.png": ("PNG", src.convert("RGBA")),
            "la.png": ("PNG", src.convert("LA")),
            "rgb.bmp": ("BMP", src),
            "rgb.tif": ("TIFF", src),
            "rgb.gif": ("GIF", src.convert("P")),
            "rgb.webp": ("WEBP", src),
        }
        for name, (fmt, im) in made.items():
            p = self.path(name)
            try:
                im.save(p, format=fmt)
            except (OSError, KeyError, ValueError):
                continue                                     # this Pillow build lacks the codec
            self._ok(p)

    def test_transparent_png_does_not_crash_or_go_see_through(self):
        p = self.path("alpha.png")
        im = Image.new("RGBA", (500, 800), (255, 0, 0, 0))   # fully transparent red
        im.save(p)
        r = self._ok(p)
        self.assertEqual(r.image.mode, "RGB")

    def test_16_bit_png_keeps_its_gradient(self):
        p = self.path("sixteen.png")
        ramp = Image.linear_gradient("L").resize((600, 900)).convert("I")
        ramp = ramp.point(lambda v: v * 257)
        ramp.save(p)                                         # mode I -> 16-bit PNG
        r = self._ok(p, template=1, title="", subtitle="", author="", publisher="")
        lum = r.image.convert("L")
        top = lum.getpixel((400, 40))
        bottom = lum.getpixel((400, 1100))
        self.assertGreater(abs(bottom - top), 15, "a 16-bit gradient was flattened to one gray")

    def test_animated_gif_uses_first_frame(self):
        p = self.path("anim.gif")
        frames = [Image.new("P", (200, 300), c) for c in (1, 200)]
        frames[0].save(p, save_all=True, append_images=frames[1:])
        self._ok(p)

    def test_exif_orientation_is_applied(self):
        """A photo stored sideways with EXIF rotate-me must come out upright."""
        # EXIF 6 = rotate 90 clockwise to view, 8 = 90 counter-clockwise, 3 = 180
        for orientation, expect_top_left in ((1, "red"), (3, "blue"), (6, "yellow"), (8, "green")):
            raw = Image.new("RGB", (300, 200), "white")
            colors = {"red": (255, 0, 0), "green": (0, 160, 0), "blue": (0, 0, 255), "yellow": (255, 255, 0)}
            # raw top-left red, top-right green, bottom-left yellow, bottom-right blue
            for (x0, y0), name in (((0, 0), "red"), ((150, 0), "green"), ((0, 100), "yellow"), ((150, 100), "blue")):
                raw.paste(Image.new("RGB", (150, 100), colors[name]), (x0, y0))
            exif = Image.Exif()
            exif[0x0112] = orientation
            p = self.path(f"orient{orientation}.jpg")
            raw.save(p, quality=95, exif=exif)
            from PIL import ImageOps
            ref = ImageOps.exif_transpose(Image.open(p)).convert("RGB")
            tl = ref.getpixel((5, 5))
            want = colors[expect_top_left]
            self.assertTrue(all(abs(a - b) < 40 for a, b in zip(tl, want)), (orientation, tl))
            # the cover background must show the same colors in the same corners as the reference
            bg = render.load_background(p, 600, 900)[0]
            rt = ImageOps.fit(ref, (600, 900), centering=(0.5, 0.5))

            def chroma(px):
                s = sum(px) or 1
                return [c / s for c in px]

            for pt in ((20, 20), (580, 20), (20, 880), (580, 880)):
                ca, cb = chroma(bg.getpixel(pt)), chroma(rt.getpixel(pt))
                self.assertLess(sum(abs(x - y) for x, y in zip(ca, cb)), 0.25, (orientation, pt))

    def test_big_exif_rotated_jpeg_through_the_fast_decoder(self):
        """Large rotated JPEG takes the draft (reduced-size decode) path."""
        raw = Image.new("RGB", (3000, 2000), "white")
        raw.paste(Image.new("RGB", (1500, 1000), (255, 0, 0)), (0, 0))        # top-left red
        exif = Image.Exif()
        exif[0x0112] = 6                                                       # rotate 90 clockwise to view
        p = self.path("bigrot.jpg")
        raw.save(p, quality=90, exif=exif)
        bg = render.load_background(p, 400, 600)[0]
        # after rotating 90 CW the raw top-left red block ends up at the top-right
        r, g, b = bg.getpixel((380, 20))
        self.assertGreater(r, g + 20)
        r2, g2, b2 = bg.getpixel((20, 580))
        self.assertLess(abs(r2 - g2), 25)

    def test_orientation_does_not_trigger_a_false_low_resolution_warning(self):
        raw = Image.new("RGB", (1200, 800), "gray")          # 800x1200 after rotation == the cover size
        exif = Image.Exif()
        exif[0x0112] = 6
        p = self.path("rot_fit.jpg")
        raw.save(p, exif=exif)
        keys = [k for k, _ in cover(p).warnings]
        self.assertNotIn("warn_bg_low_res", keys)

    def test_extreme_sizes(self):
        for name, size in (("one.png", (1, 1)), ("thin.png", (1, 4000)), ("flat.png", (4000, 1)),
                           ("tiny.png", (20, 30)), ("square.png", (1000, 1000)), ("wide.png", (6000, 600))):
            p = self.path(name)
            Image.new("RGB", size, (90, 120, 200)).save(p)
            r = self._ok(p)
            if max(size) < 800:
                self.assertIn("warn_bg_low_res", [k for k, _ in r.warnings], name)

    def test_low_res_is_reported_only_when_upscaled(self):
        p = self.path("exact.png")
        Image.new("RGB", (800, 1200), "gray").save(p)
        self.assertNotIn("warn_bg_low_res", [k for k, _ in cover(p).warnings])
        p2 = self.path("almost.png")
        Image.new("RGB", (799, 1200), "gray").save(p2)
        self.assertIn("warn_bg_low_res", [k for k, _ in cover(p2).warnings])

    def test_cropping_is_centered(self):
        """A wide photo: the middle is kept, the sides are cut."""
        p = self.path("bands.png")
        im = Image.new("RGB", (3000, 2000), (0, 0, 255))
        im.paste(Image.new("RGB", (1000, 2000), (255, 0, 0)), (1000, 0))      # red middle third
        im.save(p)
        bg = render.load_background(p, 600, 900)[0]
        mid, side = bg.getpixel((300, 450)), bg.getpixel((5, 450))
        self.assertGreater(mid[0], mid[2] + 10)            # red in the middle
        self.assertGreater(side[2], side[0] + 10)          # blue at the side


class Broken(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def path(self, name):
        return os.path.join(self.tmp.name, name)

    def test_unreadable_inputs_raise_one_clean_error(self):
        buf = io.BytesIO()
        Image.new("RGB", (800, 600), "orange").save(buf, "JPEG")
        good = buf.getvalue()
        files = {
            "zero.jpg": b"",
            "text.jpg": b"this is not an image",
            "truncated.jpg": good[: len(good) // 2],
            "header_only.png": b"\x89PNG\r\n\x1a\n",
            "html.png": b"<html></html>",
        }
        for name, data in files.items():
            p = self.path(name)
            open(p, "wb").write(data)
            before = sha(p)
            with self.assertRaises(render.BackgroundOpenError, msg=name):
                cover(p)
            self.assertEqual(sha(p), before)

    def test_missing_path_and_directory(self):
        with self.assertRaises(render.BackgroundOpenError):
            cover(self.path("nope.jpg"))
        d = self.path("folder.jpg")
        os.mkdir(d)
        with self.assertRaises(render.BackgroundOpenError):
            cover(d)

    def test_decompression_bomb_is_an_error_not_a_hang(self):
        p = self.path("bomb.png")
        Image.new("L", (20000, 10000), 7).save(p, optimize=False, compress_level=9)   # 200 MP, tiny file
        t0 = time.perf_counter()
        with self.assertRaises(render.BackgroundOpenError):
            cover(p)
        self.assertLess(time.perf_counter() - t0, 10)

    def test_large_but_legal_photo_is_fast_and_bounded(self):
        p = self.path("big.jpg")
        Image.effect_noise((9000, 6000), 50).convert("RGB").save(p, quality=70)       # 54 MP
        t0 = time.perf_counter()
        r = cover(p)
        self.assertLess(time.perf_counter() - t0, 8)
        self.assertEqual(r.image.size, (800, 1200))

    def test_unusual_paths(self):
        folder = self.path("한글 폴더 with space & #hash %20 (괄호)")
        os.makedirs(folder)
        p = os.path.join(folder, "배경 사진 #1 & 2.jpg")
        Image.new("RGB", (900, 1300), "teal").save(p)
        self.assertEqual(cover(p).image.size, (800, 1200))
        deep = self.path("d" * 60)
        for _ in range(3):
            deep = os.path.join(deep, "e" * 60)
        os.makedirs("\\\\?\\" + os.path.abspath(deep) if os.name == "nt" else deep, exist_ok=True)
        q = os.path.join(deep, "x.png")
        try:
            Image.new("RGB", (900, 1300), "teal").save(q)
        except OSError:
            self.skipTest("long paths are disabled on this system")
        self.assertEqual(cover(q).image.size, (800, 1200))

    def test_read_only_file_stays_read_only_and_unchanged(self):
        p = make_photo(self.path("ro.png"), "mid", (900, 1300))
        os.chmod(p, stat.S_IREAD)
        before = (sha(p), os.stat(p).st_mode, os.stat(p).st_mtime_ns)
        try:
            cover(p)
            self.assertEqual((sha(p), os.stat(p).st_mode, os.stat(p).st_mtime_ns), before)
        finally:
            os.chmod(p, stat.S_IWRITE | stat.S_IREAD)


class Cache(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_edited_file_is_picked_up(self):
        p = os.path.join(self.tmp.name, "a.png")
        Image.new("RGB", (900, 1300), (200, 0, 0)).save(p)
        first = cover(p).image.getpixel((10, 10))
        time.sleep(0.05)
        Image.new("RGB", (900, 1300), (0, 0, 200)).save(p)
        os.utime(p, ns=(time.time_ns() + 10**9, time.time_ns() + 10**9))
        second = cover(p).image.getpixel((10, 10))
        self.assertGreater(first[0], first[2])
        self.assertGreater(second[2], second[0])

    def test_cached_background_is_not_painted_over(self):
        p = make_photo(os.path.join(self.tmp.name, "b.png"), "gradient", (900, 1300))
        a = render.to_bytes(cover(p, template=1, title="가나다").image, "png")
        cover(p, template=2, title="완전히 다른 제목", subtitle="x" * 30)
        cover(p, template=4, title="again", series="s")
        b = render.to_bytes(cover(p, template=1, title="가나다").image, "png")
        self.assertEqual(a, b)

    def test_second_render_is_fast(self):
        p = os.path.join(self.tmp.name, "c.jpg")
        Image.effect_noise((6000, 4000), 60).convert("RGB").save(p, quality=85)
        cover(p)
        t0 = time.perf_counter()
        for _ in range(5):
            cover(p, title=str(_))
        self.assertLess((time.perf_counter() - t0) / 5, 0.15)

    def test_threads_share_the_cache_safely(self):
        p = make_photo(os.path.join(self.tmp.name, "d.png"), "noise", (1600, 2400))
        errors, outs = [], []

        def work(i):
            try:
                tpl = i % 4 + 1
                outs.append((tpl, render.to_bytes(cover(p, template=tpl, title="same").image, "png")))
            except Exception as exc:  # noqa
                errors.append(repr(exc))

        ths = [threading.Thread(target=work, args=(i,)) for i in range(12)]
        [t.start() for t in ths]
        [t.join() for t in ths]
        self.assertEqual(errors, [])
        self.assertEqual(len(outs), 12)
        for tpl in (1, 2, 3, 4):                           # same template -> byte-identical result in every thread
            self.assertEqual(len({b for t, b in outs if t == tpl}), 1, tpl)

    def test_cache_stays_bounded(self):
        for i in range(render.BG_CACHE_SIZE + 4):
            p = os.path.join(self.tmp.name, f"e{i}.png")
            Image.new("RGB", (500, 700), (i * 10, 0, 0)).save(p)
            cover(p)
        self.assertLessEqual(len(render._bg_cache), render.BG_CACHE_SIZE)


if __name__ == "__main__":
    unittest.main()
