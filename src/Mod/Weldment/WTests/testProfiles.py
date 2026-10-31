# SPDX-License-Identifier: LGPL-2.1-or-later
"""Tests for the Weldment profile tables (slice 5a, check 2).

Every row of every table is built as a cross-section face and compared with the
published section area (1 %) and the loader's analytic area (1e-9 relative).
"""

import os
import sys
import tempfile
import unittest

try:
    import Profiles
except ImportError:  # run from a checkout without the module on sys.path
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import Profiles

FAMILIES = ["EN IPE", "EN HEA", "EN HEB", "EN SHS", "EN RHS", "EN CHS", "EN L",
            "AISC W", "AISC HSS Rect", "AISC HSS Round", "AISC L", "JIS H"]
KIND = {"EN IPE": "I", "EN HEA": "I", "EN HEB": "I", "EN SHS": "RHS", "EN RHS": "RHS",
        "EN CHS": "CHS", "EN L": "L", "AISC W": "I", "AISC HSS Rect": "RHS",
        "AISC HSS Round": "CHS", "AISC L": "L", "JIS H": "I"}

__all__ = ["TestProfileTables", "TestProfileGeometry", "TestProfileLoaderErrors"]


class TestProfileTables(unittest.TestCase):
    def test_families_and_counts(self):
        self.assertEqual(Profiles.families(), FAMILIES)
        for fam in FAMILIES:
            self.assertGreaterEqual(len(Profiles.sizes(fam)), 15, fam)

    def test_every_row_cites_a_source_and_published_values(self):
        for fam in FAMILIES:
            for size in Profiles.sizes(fam):
                p = Profiles.get(fam, size)
                self.assertEqual((p.family, p.size, p.kind), (fam, size, KIND[fam]))
                self.assertTrue(p.source.startswith("http"), (fam, size))
                self.assertGreater(p.published_area, 0.0, (fam, size))
                self.assertGreater(p.mass_per_m, 0.0, (fam, size))

    def test_published_mass_agrees_with_published_area_per_family_median(self):
        # Sanity of the transcription: mass / (area * 7850 kg/m^3) is close to 1 for the
        # bulk of every family (AISC HSS masses use the nominal wall, hence the 10 % band).
        for fam in FAMILIES:
            ratios = sorted(Profiles.get(fam, s).mass_per_m /
                            (Profiles.get(fam, s).published_area * 7.85e-3)
                            for s in Profiles.sizes(fam))
            med = ratios[len(ratios) // 2]
            self.assertLess(abs(med - 1.0), 0.10, (fam, med))


class TestProfileGeometry(unittest.TestCase):
    summary = {}

    def test_all_rows_build_and_match_published_and_analytic_area(self):
        failures = []
        checked = 0
        max_pub = 0.0
        max_ana = 0.0
        for fam in Profiles.families():
            fam_pub = 0.0
            for size in Profiles.sizes(fam):
                p = Profiles.get(fam, size)
                checked += 1
                try:
                    face = p.face()
                except Exception as exc:  # report every bad row at once
                    failures.append("%s %s: face() raised %s" % (fam, size, exc))
                    continue
                if not face.isValid() or len(face.Faces) != 1:
                    failures.append("%s %s: face is not one valid face" % (fam, size))
                    continue
                bb = face.BoundBox
                if bb.ZMin != 0.0 or bb.ZMax != 0.0:
                    failures.append("%s %s: face not in the XY plane" % (fam, size))
                n = face.normalAt(0, 0) if hasattr(face, "normalAt") else None
                if n is not None and abs(abs(n.z) - 1.0) > 1e-12:
                    failures.append("%s %s: face normal is not Z" % (fam, size))
                dev_pub = abs(face.Area / p.published_area - 1.0)
                dev_ana = abs(face.Area / p.area() - 1.0)
                fam_pub = max(fam_pub, dev_pub)
                max_pub = max(max_pub, dev_pub)
                max_ana = max(max_ana, dev_ana)
                if dev_pub > 0.01:
                    failures.append("%s %s: area %.1f vs published %.1f (%.2f %%)" %
                                    (fam, size, face.Area, p.published_area, dev_pub * 100))
                if dev_ana > 1e-9:
                    failures.append("%s %s: area %.9g vs analytic %.9g" %
                                    (fam, size, face.Area, p.area()))
                com = face.CenterOfMass
                if max(abs(com.x), abs(com.y), abs(com.z)) > 1e-9:
                    failures.append("%s %s: centroid (%g, %g, %g) not at origin" %
                                    (fam, size, com.x, com.y, com.z))
                x0, x1, y0, y1 = p.bbox()
                if max(abs(x0 - bb.XMin), abs(x1 - bb.XMax), abs(y0 - bb.YMin),
                       abs(y1 - bb.YMax)) > 1e-9:
                    failures.append("%s %s: bbox() disagrees with the face" % (fam, size))
                if not (x0 < 0.0 < x1 and y0 < 0.0 < y1):
                    failures.append("%s %s: origin outside the bbox" % (fam, size))
            TestProfileGeometry.summary[fam] = fam_pub
        TestProfileGeometry.summary["_rows"] = checked
        TestProfileGeometry.summary["_max_pub"] = max_pub
        TestProfileGeometry.summary["_max_ana"] = max_ana
        print("\nprofiles: %d rows checked, max |area/published - 1| = %.3f %%, "
              "max |area/analytic - 1| = %.2e" % (checked, max_pub * 100, max_ana))
        self.assertEqual(failures, [], "\n".join(failures[:30]))

    def test_symmetric_sections_are_centred_on_their_bounding_box(self):
        for fam, size in (("EN IPE", "200"), ("EN HEB", "300"), ("EN SHS", "100x100x5"),
                          ("EN RHS", "150x100x5"), ("EN CHS", "168.3x6.3"),
                          ("AISC W", "W14X90"), ("JIS H", "300x300x10x15")):
            x0, x1, y0, y1 = Profiles.get(fam, size).bbox()
            self.assertAlmostEqual(x0 + x1, 0.0, delta=1e-9, msg=(fam, size))
            self.assertAlmostEqual(y0 + y1, 0.0, delta=1e-9, msg=(fam, size))

    def test_dimensions_and_orientation(self):
        p = Profiles.get("EN IPE", "200")      # width along x, height along y
        x0, x1, y0, y1 = p.bbox()
        self.assertAlmostEqual(x1 - x0, p.dims["b"], delta=1e-9)
        self.assertAlmostEqual(y1 - y0, p.dims["h"], delta=1e-9)
        p = Profiles.get("EN RHS", "150x100x5")
        x0, x1, y0, y1 = p.bbox()
        self.assertAlmostEqual(x1 - x0, p.dims["b"], delta=1e-9)
        self.assertAlmostEqual(y1 - y0, p.dims["h"], delta=1e-9)
        p = Profiles.get("EN L", "150x150x12")  # legs along +x/+y before centring
        x0, x1, y0, y1 = p.bbox()
        self.assertAlmostEqual(x1 - x0, 150.0, delta=1e-9)
        self.assertGreater(abs(x0), 0.0)
        self.assertLess(abs(x0), 75.0)          # centroid is inside the corner region
        p = Profiles.get("EN CHS", "168.3x6.3")
        x0, x1, y0, y1 = p.bbox()
        self.assertAlmostEqual(x1 - x0, 168.3, delta=1e-9)

    def test_face_returns_an_independent_copy(self):
        p = Profiles.get("EN IPE", "200")
        f = p.face()
        f.translate(f.CenterOfMass.__class__(10, 0, 0))
        com = p.face().CenterOfMass
        self.assertAlmostEqual(com.x, 0.0, delta=1e-9)


class TestProfileLoaderErrors(unittest.TestCase):
    HEADER = ("family,size,kind,h,b,tw,tf,r,t,ro,ri,d,r1,r2,area_mm2,mass_kg_m,source\n")

    def _load(self, body, header=None):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "bad.csv")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("# SPDX-License-Identifier: LGPL-2.1-or-later\n")
                fh.write(self.HEADER if header is None else header)
                fh.write(body)
            return Profiles._read_file(path, {})

    def test_comment_lines_and_a_good_row_load(self):
        self.assertIsNone(self._load("T,1,CHS,,,,,,5,,,100,,,1500,11.8,https://example.org/t\n"))

    def test_non_numeric_value_names_file_line_and_column(self):
        with self.assertRaises(Profiles.ProfileTableError) as cm:
            self._load("T,1,CHS,,,,,,five,,,100,,,1500,11.8,https://example.org/t\n")
        msg = str(cm.exception)
        self.assertIn("bad.csv:3", msg)
        self.assertIn("'t'", msg)

    def test_missing_required_dimension(self):
        with self.assertRaises(Profiles.ProfileTableError) as cm:
            self._load("T,1,I,200,100,,8,12,,,,,,,2850,22.4,https://example.org/t\n")
        self.assertIn("'tw'", str(cm.exception))

    def test_unknown_kind_missing_source_and_duplicate(self):
        with self.assertRaises(Profiles.ProfileTableError) as cm:
            self._load("T,1,Z,,,,,,,,,,,,1,1,https://example.org/t\n")
        self.assertIn("unknown kind", str(cm.exception))
        with self.assertRaises(Profiles.ProfileTableError) as cm:
            self._load("T,1,CHS,,,,,,5,,,100,,,1500,11.8,\n")
        self.assertIn("source", str(cm.exception))
        row = "T,1,CHS,,,,,,5,,,100,,,1500,11.8,https://example.org/t\n"
        with self.assertRaises(Profiles.ProfileTableError) as cm:
            self._load(row + row)
        self.assertIn("duplicate", str(cm.exception))

    def test_missing_header_column_and_wrong_field_count(self):
        with self.assertRaises(Profiles.ProfileTableError) as cm:
            self._load("", header="family,size,kind\n")
        self.assertIn("lacks column", str(cm.exception))
        with self.assertRaises(Profiles.ProfileTableError) as cm:
            self._load("T,1,CHS\n")
        self.assertIn("expected 17 fields", str(cm.exception))

    def test_unknown_family_and_size_are_readable_key_errors(self):
        with self.assertRaises(KeyError) as cm:
            Profiles.get("EN IPX", "200")
        self.assertIn("EN IPE", str(cm.exception))
        with self.assertRaises(KeyError) as cm:
            Profiles.get("EN IPE", "9999")
        self.assertIn("9999", str(cm.exception))
        with self.assertRaises(KeyError):
            Profiles.sizes("nope")


if __name__ == "__main__":
    unittest.main()
