# SPDX-License-Identifier: LGPL-2.1-or-later

#  Copyright (c) 2007 Jürgen Riegel <juergen.riegel@web.de>
#  LGPL

import os
import sys
import io
import FreeCAD, unittest, Mesh
import MeshEnums
from FreeCAD import Base
import time, tempfile, math

# http://python-kurs.eu/threads.php
try:
    import _thread as thread
except Exception:
    import thread

from os.path import join

# ---------------------------------------------------------------------------
# define the functions to test the FreeCAD mesh module
# ---------------------------------------------------------------------------


class MeshTopoTestCases(unittest.TestCase):
    def setUp(self):
        # set up a planar face with 18 triangles
        self.planarMesh = []
        for x in range(3):
            for y in range(3):
                self.planarMesh.append([0.0 + x, 0.0 + y, 0.0000])
                self.planarMesh.append([1.0 + x, 1.0 + y, 0.0000])
                self.planarMesh.append([0.0 + x, 1.0 + y, 0.0000])
                self.planarMesh.append([0.0 + x, 0.0 + y, 0.0000])
                self.planarMesh.append([1.0 + x, 0.0 + y, 0.0000])
                self.planarMesh.append([1.0 + x, 1.0 + y, 0.0000])

    def testCollapseFacetsSingle(self):
        for i in range(18):
            planarMeshObject = Mesh.Mesh(self.planarMesh)
            planarMeshObject.collapseFacets([i])

    def testCollapseFacetsMultible(self):
        planarMeshObject = Mesh.Mesh(self.planarMesh)
        planarMeshObject.collapseFacets(range(7))

    def testCollapseFacetsAll(self):
        planarMeshObject = Mesh.Mesh(self.planarMesh)
        planarMeshObject.collapseFacets(range(18))

    # fmt: off
    def testCorruptedFacet(self):
        v = FreeCAD.Vector
        mesh = Mesh.Mesh()
        mesh.addFacet(
        v(1.0e1, -1.0e1, 1.0e1),
        v(1.0e1, +1.0e1, 1.0e1),
        v(0.0e0, 0.0e0, 1.0e1))

        mesh.addFacet(
        v(-1.0e1, -1.0e1, 1.0e1),
        v(-1.0e1, +1.0e1, 1.0e1),
        v(0e0, 0.0e0, 1.0e1))

        mesh.addFacet(
        v(+1.0e1, +1.0e1, 1.0e1),
        v(-1.0e1, +1.0e1, 1.0e1),
        v(.0e0, 0.0e0, 1.0e1))

        mesh.addFacet(
        v(+1.0e1, -1.0e1, 1.0e1),
        v(-1.0e1, -1.0e1, 1.0e1),
        v(.0e0, 0.0e0, 1.0e1))

        mesh.addFacet(
        v(-1.0e1, +1.0e1, 1.0e1),
        v(+1.0e1, +1.0e1, 1.0e1),
        v(+1.0e1, +1.0e1, 1.0e1))

        mesh.addFacet(
        v(+1.0e1, +1.0e1, 1.0e1),
        v(+1.0e1, 00.0e1, 1.0e1),
        v(+1.0e1, -1.0e1, 1.0e1))

        self.assertEqual(mesh.CountFacets, 6)
        mesh.fixIndices()
        self.assertEqual(mesh.CountFacets, 5)


# fmt: on


class MeshSplitTestCases(unittest.TestCase):
    def setUp(self):
        self.mesh = Mesh.createBox(1.0, 1.0, 1.0)

    def testSplitFacetOnOneEdge(self):
        p1 = self.mesh.Points[0].Vector
        p2 = self.mesh.Points[1].Vector
        p3 = self.mesh.Points[2].Vector
        self.mesh.splitFacet(0, p1, (p2 + p3) / 2)
        self.assertFalse(self.mesh.hasNonManifolds())
        self.assertFalse(self.mesh.hasInvalidNeighbourhood())
        self.assertFalse(self.mesh.hasPointsOutOfRange())
        self.assertFalse(self.mesh.hasFacetsOutOfRange())
        self.assertFalse(self.mesh.hasCorruptedFacets())
        self.assertTrue(self.mesh.isSolid())

    def testSplitFacetOnTwoEdges_21(self):
        p1 = self.mesh.Points[0].Vector
        p2 = self.mesh.Points[1].Vector
        p3 = self.mesh.Points[2].Vector
        self.mesh.splitFacet(0, (p1 + p3) / 2, (p2 + p3) / 2)
        self.assertFalse(self.mesh.hasNonManifolds())
        self.assertFalse(self.mesh.hasInvalidNeighbourhood())
        self.assertFalse(self.mesh.hasPointsOutOfRange())
        self.assertFalse(self.mesh.hasFacetsOutOfRange())
        self.assertFalse(self.mesh.hasCorruptedFacets())
        self.assertTrue(self.mesh.isSolid())

    def testSplitFacetOnTwoEdges_12(self):
        p1 = self.mesh.Points[0].Vector
        p2 = self.mesh.Points[1].Vector
        p3 = self.mesh.Points[2].Vector
        self.mesh.splitFacet(0, (p2 + p3) / 2, (p1 + p3) / 2)
        self.assertFalse(self.mesh.hasNonManifolds())
        self.assertFalse(self.mesh.hasInvalidNeighbourhood())
        self.assertFalse(self.mesh.hasPointsOutOfRange())
        self.assertFalse(self.mesh.hasFacetsOutOfRange())
        self.assertFalse(self.mesh.hasCorruptedFacets())
        self.assertTrue(self.mesh.isSolid())

    def testSplitFacetOnTwoEdges_01(self):
        p1 = self.mesh.Points[0].Vector
        p2 = self.mesh.Points[1].Vector
        p3 = self.mesh.Points[2].Vector
        self.mesh.splitFacet(0, (p1 + p2) / 2, (p2 + p3) / 2)
        self.assertFalse(self.mesh.hasNonManifolds())
        self.assertFalse(self.mesh.hasInvalidNeighbourhood())
        self.assertFalse(self.mesh.hasPointsOutOfRange())
        self.assertFalse(self.mesh.hasFacetsOutOfRange())
        self.assertFalse(self.mesh.hasCorruptedFacets())
        self.assertTrue(self.mesh.isSolid())

    def testSplitFacetOnTwoEdges_10(self):
        p1 = self.mesh.Points[0].Vector
        p2 = self.mesh.Points[1].Vector
        p3 = self.mesh.Points[2].Vector
        self.mesh.splitFacet(0, (p2 + p3) / 2, (p1 + p2) / 2)
        self.assertFalse(self.mesh.hasNonManifolds())
        self.assertFalse(self.mesh.hasInvalidNeighbourhood())
        self.assertFalse(self.mesh.hasPointsOutOfRange())
        self.assertFalse(self.mesh.hasFacetsOutOfRange())
        self.assertFalse(self.mesh.hasCorruptedFacets())
        self.assertTrue(self.mesh.isSolid())

    def testSplitFacetOnTwoEdges_02(self):
        p1 = self.mesh.Points[0].Vector
        p2 = self.mesh.Points[1].Vector
        p3 = self.mesh.Points[2].Vector
        self.mesh.splitFacet(0, (p1 + p2) / 2, (p1 + p3) / 2)
        self.assertFalse(self.mesh.hasNonManifolds())
        self.assertFalse(self.mesh.hasInvalidNeighbourhood())
        self.assertFalse(self.mesh.hasPointsOutOfRange())
        self.assertFalse(self.mesh.hasFacetsOutOfRange())
        self.assertFalse(self.mesh.hasCorruptedFacets())
        self.assertTrue(self.mesh.isSolid())

    def testSplitFacetOnTwoEdges_20(self):
        p1 = self.mesh.Points[0].Vector
        p2 = self.mesh.Points[1].Vector
        p3 = self.mesh.Points[2].Vector
        self.mesh.splitFacet(0, (p1 + p3) / 2, (p1 + p2) / 2)
        self.assertFalse(self.mesh.hasNonManifolds())
        self.assertFalse(self.mesh.hasInvalidNeighbourhood())
        self.assertFalse(self.mesh.hasPointsOutOfRange())
        self.assertFalse(self.mesh.hasFacetsOutOfRange())
        self.assertFalse(self.mesh.hasCorruptedFacets())
        self.assertTrue(self.mesh.isSolid())

    def testSplitFacetOnTwoEdges_5teps(self):
        Vec3d = FreeCAD.Vector
        for i in range(5):
            f = self.mesh.Facets[0]
            p1 = Vec3d(f.Points[0])
            p2 = Vec3d(f.Points[1])
            p3 = Vec3d(f.Points[2])
            self.mesh.splitFacet(0, (p1 + p3) / 2, (p2 + p3) / 2)

        self.assertFalse(self.mesh.hasNonManifolds())
        self.assertFalse(self.mesh.hasInvalidNeighbourhood())
        self.assertFalse(self.mesh.hasPointsOutOfRange())
        self.assertFalse(self.mesh.hasFacetsOutOfRange())
        self.assertFalse(self.mesh.hasCorruptedFacets())
        self.assertTrue(self.mesh.isSolid())

    def testFindNearest(self):
        self.assertEqual(len(self.mesh.nearestFacetOnRay((-2, 2, -6), (0, 0, 1))), 0)
        self.assertEqual(len(self.mesh.nearestFacetOnRay((0.5, 0.5, 0.5), (0, 0, 1))), 1)
        self.assertEqual(
            len(self.mesh.nearestFacetOnRay((0.5, 0.5, 0.5), (0, 0, 1), -math.pi / 2)), 0
        )
        self.assertEqual(
            len(self.mesh.nearestFacetOnRay((0.2, 0.1, 0.2), (0, 0, 1))),
            len(self.mesh.nearestFacetOnRay((0.2, 0.1, 0.2), (0, 0, -1))),
        )
        self.assertEqual(
            len(self.mesh.nearestFacetOnRay((0.2, 0.1, 0.2), (0, 0, 1), math.pi / 2)),
            len(self.mesh.nearestFacetOnRay((0.2, 0.1, 0.2), (0, 0, -1), math.pi / 2)),
        )
        # Apply placement to mesh
        plm = Base.Placement(Base.Vector(1, 2, 3), Base.Rotation(1, 1, 1, 1))
        pnt = Base.Vector(0.5, 0.5, 0.5)
        vec = Base.Vector(0.0, 0.0, 1.0)

        self.mesh.Placement = plm
        self.assertEqual(len(self.mesh.nearestFacetOnRay(pnt, vec)), 0)

        # Apply the placement on the ray as well
        pnt = plm.multVec(pnt)
        vec = plm.Rotation.multVec(vec)
        self.assertEqual(len(self.mesh.nearestFacetOnRay(pnt, vec)), 1)

    def testForaminate(self):
        class FilterAngle:
            def __init__(self, mesh, vec, limit):
                self.myMesh = mesh
                self.vec = vec
                self.limit = limit

            def check_angle(self, item):
                angle = self.myMesh.Facets[item].Normal.getAngle(self.vec)
                return angle < self.limit

        results = self.mesh.foraminate((0.0, 0.0, 0.0), (0, 1, 1))
        filtered_result = list(
            filter(
                FilterAngle(self.mesh, FreeCAD.Vector(0, 1, 1), math.pi / 2).check_angle,
                results.keys(),
            )
        )

        self.assertEqual(
            filtered_result, list(self.mesh.foraminate((0.0, 0.0, 0.0), (0, 1, 1), math.pi / 2))
        )

    def testForaminatePlacement(self):
        pnt = Base.Vector(0.0, 0.0, 0.0)
        vec = Base.Vector(0.0, 1.0, 1.0)
        results = self.mesh.foraminate(pnt, vec)
        self.assertEqual(len(results), 4)

        # Apply placement to mesh
        plm = Base.Placement(Base.Vector(1, 2, 3), Base.Rotation(1, 1, 1, 1))
        self.mesh.Placement = plm
        self.assertEqual(len(self.mesh.foraminate(pnt, vec)), 0)

        # Apply the placement on the ray as well
        pnt = plm.multVec(pnt)
        vec = plm.Rotation.multVec(vec)
        results2 = self.mesh.foraminate(pnt, vec)
        self.assertEqual(len(results2), 4)
        self.assertEqual(list(results), list(results2))


class MeshGeoTestCases(unittest.TestCase):
    def setUp(self):
        # set up a planar face with 2 triangles
        self.planarMesh = []

    def testIntersection(self):
        self.planarMesh.append([0.9961, 1.5413, 4.3943])
        self.planarMesh.append([9.4796, 10.024, -3.0937])
        self.planarMesh.append([1.4308, 11.3841, 2.6829])
        self.planarMesh.append([2.6493, 2.2536, 3.0679])
        self.planarMesh.append([13.1126, 0.4857, -4.4417])
        self.planarMesh.append([10.2410, 8.9040, -3.5002])
        planarMeshObject = Mesh.Mesh(self.planarMesh)
        f1 = planarMeshObject.Facets[0]
        f2 = planarMeshObject.Facets[1]
        res = f1.intersect(f2)
        self.assertTrue(len(res) == 0)

    def testIntersection2(self):
        self.planarMesh.append([-16.097176, -29.891157, 15.987688])
        self.planarMesh.append([-16.176304, -29.859991, 15.947966])
        self.planarMesh.append([-16.071451, -29.900553, 15.912505])
        self.planarMesh.append([-16.092241, -29.893408, 16.020439])
        self.planarMesh.append([-16.007210, -29.926180, 15.967641])
        self.planarMesh.append([-16.064457, -29.904951, 16.090832])
        planarMeshObject = Mesh.Mesh(self.planarMesh)
        f1 = planarMeshObject.Facets[0]
        f2 = planarMeshObject.Facets[1]
        # does definitely NOT intersect
        res = f1.intersect(f2)
        self.assertTrue(len(res) == 0)

    def testIntersectionOfTransformedMesh(self):
        self.planarMesh.append([0.0, 10.0, 10.0])
        self.planarMesh.append([10.0, 0.0, 10.0])
        self.planarMesh.append([10.0, 10.0, 10.0])
        self.planarMesh.append([6.0, 8.0, 10.0])
        self.planarMesh.append([16.0, 8.0, 10.0])
        self.planarMesh.append([6.0, 18.0, 10.0])
        planarMeshObject = Mesh.Mesh(self.planarMesh)

        mat = Base.Matrix()
        mat.rotateX(1.0)
        mat.rotateY(1.0)
        mat.rotateZ(1.0)
        planarMeshObject.transformGeometry(mat)

        f1 = planarMeshObject.Facets[0]
        f2 = planarMeshObject.Facets[1]
        res = f1.intersect(f2)
        self.assertEqual(len(res), 2)

    def testIntersectionOfParallelTriangles(self):
        self.planarMesh.append([0.0, 10.0, 10.0])
        self.planarMesh.append([10.0, 0.0, 10.0])
        self.planarMesh.append([10.0, 10.0, 10.0])
        self.planarMesh.append([6.0, 8.0, 10.1])
        self.planarMesh.append([16.0, 8.0, 10.1])
        self.planarMesh.append([6.0, 18.0, 10.1])
        planarMeshObject = Mesh.Mesh(self.planarMesh)

        mat = Base.Matrix()
        mat.rotateX(1.0)
        mat.rotateY(1.0)
        mat.rotateZ(1.0)
        planarMeshObject.transformGeometry(mat)

        f1 = planarMeshObject.Facets[0]
        f2 = planarMeshObject.Facets[1]
        res = f1.intersect(f2)
        self.assertTrue(len(res) == 0)

    def testIntersectionOnEdge(self):
        self.planarMesh.append([5.0, -1.9371663331985474, 0.49737977981567383])
        self.planarMesh.append([4.0, -1.9371663331985474, 0.49737977981567383])
        self.planarMesh.append([5.0, -1.9842294454574585, 0.25066646933555603])
        self.planarMesh.append([4.6488823890686035, -1.7827962636947632, 0.4577442705631256])
        self.planarMesh.append([4.524135112762451, -2.0620131492614746, 0.5294350385665894])
        self.planarMesh.append([4.6488823890686035, -1.8261089324951172, 0.23069120943546295])
        planarMeshObject = Mesh.Mesh(self.planarMesh)
        f1 = planarMeshObject.Facets[0]
        f2 = planarMeshObject.Facets[1]
        res = f1.intersect(f2)
        self.assertEqual(len(res), 2)

    def testIntersectionCoplanar(self):
        self.planarMesh.append([0.0, 10.0, 10.0])
        self.planarMesh.append([10.0, 0.0, 10.0])
        self.planarMesh.append([10.0, 10.0, 10.0])
        self.planarMesh.append([6.0, 8.0, 10.0])
        self.planarMesh.append([16.0, 8.0, 10.0])
        self.planarMesh.append([6.0, 18.0, 10.0])
        planarMeshObject = Mesh.Mesh(self.planarMesh)
        f1 = planarMeshObject.Facets[0]
        f2 = planarMeshObject.Facets[1]
        res = f1.intersect(f2)
        self.assertTrue(len(res) == 2)

    def testIntersectionOverlap(self):
        self.planarMesh.append([0.0, 0.0, 0.0])
        self.planarMesh.append([5.0, 0.0, 0.0])
        self.planarMesh.append([8.0, 5.0, 0.0])
        self.planarMesh.append([4.0, 0.0, 0.0])
        self.planarMesh.append([10.0, 0.0, 0.0])
        self.planarMesh.append([9.0, 5.0, 0.0])
        planarMeshObject = Mesh.Mesh(self.planarMesh)
        f1 = planarMeshObject.Facets[0]
        f2 = planarMeshObject.Facets[1]
        res = f1.intersect(f2)
        self.assertTrue(len(res) == 2)

    def testIntersectionOfIntersectingEdges(self):
        self.planarMesh.append([0.0, 10.0, 10.0])
        self.planarMesh.append([10.0, 0.0, 10.0])
        self.planarMesh.append([10.0, 10.0, 10.0])
        self.planarMesh.append([6.0, 8.0, 10.0])
        self.planarMesh.append([16.0, 8.0, 10.0])
        self.planarMesh.append([6.0, 18.0, 10.0])
        planarMeshObject = Mesh.Mesh(self.planarMesh)

        edge1 = planarMeshObject.Facets[0].getEdge(2)
        edge2 = planarMeshObject.Facets[1].getEdge(2)
        res = edge1.intersectWithEdge(edge2)
        self.assertTrue(len(res) == 1)
        self.assertEqual(res[0][0], 6.0)
        self.assertEqual(res[0][1], 10.0)
        self.assertEqual(res[0][2], 10.0)

    def testIntersectionOfParallelEdges(self):
        self.planarMesh.append([0.0, 10.0, 10.0])
        self.planarMesh.append([10.0, 0.0, 10.0])
        self.planarMesh.append([10.0, 10.0, 10.0])
        self.planarMesh.append([6.0, 8.0, 10.0])
        self.planarMesh.append([16.0, 8.0, 10.0])
        self.planarMesh.append([6.0, 18.0, 10.0])
        planarMeshObject = Mesh.Mesh(self.planarMesh)

        edge1 = planarMeshObject.Facets[0].getEdge(2)
        edge2 = planarMeshObject.Facets[1].getEdge(0)
        res = edge1.intersectWithEdge(edge2)
        self.assertTrue(len(res) == 0)

    def testIntersectionOfCollinearEdges(self):
        self.planarMesh.append([0.0, 0.0, 0.0])
        self.planarMesh.append([6.0, 0.0, 0.0])
        self.planarMesh.append([3.0, 4.0, 0.0])
        self.planarMesh.append([7.0, 0.0, 0.0])
        self.planarMesh.append([13.0, 0.0, 0.0])
        self.planarMesh.append([10.0, 4.0, 0.0])
        planarMeshObject = Mesh.Mesh(self.planarMesh)

        edge1 = planarMeshObject.Facets[0].getEdge(0)
        edge2 = planarMeshObject.Facets[1].getEdge(0)
        res = edge1.intersectWithEdge(edge2)
        self.assertTrue(len(res) == 0)

    def testIntersectionOfWarpedEdges(self):
        self.planarMesh.append([0.0, 0.0, 0.0])
        self.planarMesh.append([6.0, 0.0, 0.0])
        self.planarMesh.append([3.0, 4.0, 0.0])
        self.planarMesh.append([2.0, 2.0, 1.0])
        self.planarMesh.append([8.0, 2.0, 1.0])
        self.planarMesh.append([5.0, 6.0, 1.0])
        planarMeshObject = Mesh.Mesh(self.planarMesh)

        edge1 = planarMeshObject.Facets[0].getEdge(1)
        edge2 = planarMeshObject.Facets[1].getEdge(0)
        res = edge1.intersectWithEdge(edge2)
        self.assertTrue(len(res) == 0)

    def testSelfIntersection(self):
        s = b"""solid Simple
facet normal 0.0e0 0.0e0 1.0e1
    outer loop
        vertex 0.0e1 0.0e1 1.0e1
        vertex 0.0e1 +1.0e1 1.0e1
        vertex +1.0e1 0.0e1 1.0e1
    endloop
endfacet
facet normal 0.0e0 0.0e0 1.0e1
    outer loop
        vertex 0.0e1 +1.0e1 1.0e1
        vertex +1.0e1 0.0e1 1.0e1
        vertex 1.0e1 1.0e1 1.0e1
    endloop
endfacet
facet normal 0.0e0 0.0e0 1.0e1
    outer loop
        vertex 0.0e1 0.0e1 1.0e1
        vertex 0.0e1 +1.0e1 1.0e1
        vertex -1.0e1 1.0e1 1.0e1
    endloop
endfacet
facet normal 0.0e0 0.0e0 1.0e1
    outer loop
        vertex 0.0e1 0.0e1 1.0e1
        vertex +1.0e1 0.0e1 1.0e1
        vertex +1.0e1 -1.0e1 1.0e1
    endloop
endfacet
facet normal 0.0e0 0.0e0 1.0e1
    outer loop
        vertex 0.6e1 0.8e1 1.0e1
        vertex +1.6e1 0.8e1 1.0e1
        vertex +0.6e1 1.8e1 1.0e1
    endloop
endfacet
endsolid Simple"""
        mesh = Mesh.Mesh()
        data = io.BytesIO(s)
        mesh.read(Stream=data, Format="AST")
        self.assertTrue(mesh.hasSelfIntersections())


# Threads


def loadFile(name):
    # lock.acquire()
    mesh = Mesh.Mesh()
    # FreeCAD.Console.PrintMessage("Create mesh instance\n")
    # lock.release()
    mesh.read(name)
    # FreeCAD.Console.PrintMessage("Mesh loaded successfully.\n")


def createMesh(r, s):
    # FreeCAD.Console.PrintMessage("Create sphere (%s,%s)...\n"%(r,s))
    mesh = Mesh.createSphere(r, s)
    # FreeCAD.Console.PrintMessage("... destroy sphere\n")


class LoadMeshInThreadsCases(unittest.TestCase):
    def setUp(self):
        pass

    def testSphereMesh(self):
        for i in range(6, 8):
            thread.start_new(createMesh, (10.0, (i + 1) * 20))
        time.sleep(10)

    def testLoadMesh(self):
        mesh = Mesh.createSphere(10.0, 100)  # a fine sphere
        name = tempfile.gettempdir() + os.sep + "mesh.stl"
        mesh.write(name)
        # FreeCAD.Console.PrintMessage("Write mesh to %s\n"%(name))
        # lock=thread.allocate_lock()
        for i in range(2):
            thread.start_new(loadFile, (name,))
        time.sleep(1)

    def tearDown(self):
        pass


class PolynomialFitCases(unittest.TestCase):
    def setUp(self):
        pass

    def testFitGood(self):
        # symmetric
        v = []
        v.append(FreeCAD.Vector(0, 0, 0.0))
        v.append(FreeCAD.Vector(1, 0, 0.5))
        v.append(FreeCAD.Vector(2, 0, 0.0))
        v.append(FreeCAD.Vector(0, 1, 0.5))
        v.append(FreeCAD.Vector(1, 1, 1.0))
        v.append(FreeCAD.Vector(2, 1, 0.5))
        v.append(FreeCAD.Vector(0, 2, 0.0))
        v.append(FreeCAD.Vector(1, 2, 0.5))
        v.append(FreeCAD.Vector(2, 2, 0.0))
        d = Mesh.polynomialFit(v)
        c = d["Coefficients"]
        # print ("Polynomial: f(x,y)=%f*x^2%+f*y^2%+f*x*y%+f*x%+f*y%+f" % (c[0],c[1],c[2],c[3],c[4],c[5]))
        for i in d["Residuals"]:
            self.assertTrue(math.fabs(i) < 0.0001, "Too high residual %f" % math.fabs(i))

    def testFitExact(self):
        # symmetric
        v = []
        v.append(FreeCAD.Vector(0, 0, 0.0))
        v.append(FreeCAD.Vector(1, 0, 0.0))
        v.append(FreeCAD.Vector(2, 0, 0.0))
        v.append(FreeCAD.Vector(0, 1, 0.0))
        v.append(FreeCAD.Vector(1, 1, 1.0))
        v.append(FreeCAD.Vector(2, 1, 0.0))
        d = Mesh.polynomialFit(v)
        c = d["Coefficients"]
        # print ("Polynomial: f(x,y)=%f*x^2%+f*y^2%+f*x*y%+f*x%+f*y%+f" % (c[0],c[1],c[2],c[3],c[4],c[5]))
        for i in d["Residuals"]:
            self.assertTrue(math.fabs(i) < 0.0001, "Too high residual %f" % math.fabs(i))

    def testFitBad(self):
        # symmetric
        v = []
        v.append(FreeCAD.Vector(0, 0, 0.0))
        v.append(FreeCAD.Vector(1, 0, 0.0))
        v.append(FreeCAD.Vector(2, 0, 0.0))
        v.append(FreeCAD.Vector(0, 1, 0.0))
        v.append(FreeCAD.Vector(1, 1, 1.0))
        v.append(FreeCAD.Vector(2, 1, 0.0))
        v.append(FreeCAD.Vector(0, 2, 0.0))
        v.append(FreeCAD.Vector(1, 2, 0.0))
        v.append(FreeCAD.Vector(2, 2, 0.0))
        d = Mesh.polynomialFit(v)
        c = d["Coefficients"]
        # print ("Polynomial: f(x,y)=%f*x^2%+f*y^2%+f*x*y%+f*x%+f*y%+f" % (c[0],c[1],c[2],c[3],c[4],c[5]))
        for i in d["Residuals"]:
            self.assertFalse(math.fabs(i) < 0.0001, "Residual %f must be higher" % math.fabs(i))

    def tearDown(self):
        pass


class NastranReader(unittest.TestCase):
    def setUp(self):
        self.test_dir = join(FreeCAD.getHomePath(), "Mod", "Mesh", "App", "TestData")

    def testEightCharGRIDElement(self):
        m = Mesh.read(f"{self.test_dir}/NASTRAN_Test_GRID_CQUAD4.bdf")
        self.assertEqual(m.CountPoints, 10)
        self.assertEqual(m.CountFacets, 8)  # Quads split into two triangles

    def testDelimitedGRIDElement(self):
        m = Mesh.read(f"{self.test_dir}/NASTRAN_Test_Delimited_GRID_CQUAD4.bdf")
        self.assertEqual(m.CountPoints, 10)
        self.assertEqual(m.CountFacets, 8)  # Quads split into two triangles

    def testSixteenCharGRIDElement(self):
        m = Mesh.read(f"{self.test_dir}/NASTRAN_Test_GRIDSTAR_CQUAD4.bdf")
        self.assertEqual(m.CountPoints, 4)
        self.assertEqual(m.CountFacets, 2)  # Quads split into two triangles

    def testCTRIA3Element(self):
        m = Mesh.read(f"{self.test_dir}/NASTRAN_Test_GRID_CTRIA3.bdf")
        self.assertEqual(m.CountPoints, 3)
        self.assertEqual(m.CountFacets, 1)

    def tearDown(self):
        pass


class MeshSubElement(unittest.TestCase):
    def setUp(self):
        self.mesh = Mesh.createBox(1.0, 1.0, 1.0)

    def testCenterOfGravity(self):
        c = self.mesh.CenterOfGravity
        self.assertEqual(c, Base.Vector(0.0, 0.0, 0.0))

    def testSubElements(self):
        types = self.mesh.getElementTypes()
        self.assertIn("Mesh", types)
        self.assertIn("Segment", types)

    def testCountSubElements(self):
        self.assertEqual(self.mesh.countSubElements("Mesh"), 1)
        self.assertEqual(self.mesh.countSubElements("Segment"), 0)

    def testFacesFromSubElement(self):
        element = self.mesh.getFacesFromSubElement("Mesh", 0)
        self.assertIsInstance(element, tuple)
        self.assertEqual(len(element), 2)
        self.assertEqual(len(element[0]), 8)
        self.assertEqual(len(element[1]), 12)

    def testSegmentSubElement(self):
        self.mesh.addSegment([0, 2, 4, 6, 8])
        self.assertEqual(self.mesh.countSegments(), 1)
        self.assertEqual(self.mesh.countSubElements("Segment"), 1)
        element = self.mesh.getFacesFromSubElement("Segment", 0)
        self.assertIsInstance(element, tuple)
        self.assertEqual(len(element), 2)
        self.assertEqual(len(element[0]), 7)
        self.assertEqual(len(element[1]), 5)
        segment = self.mesh.meshFromSegment(self.mesh.getSegment(0))
        self.assertEqual(segment.CountPoints, 7)
        self.assertEqual(segment.CountFacets, 5)

    def tearDown(self):
        pass


class MeshProperty(unittest.TestCase):
    def setUp(self):
        self.doc = FreeCAD.newDocument("MeshTest")

    def tearDown(self):
        FreeCAD.closeDocument(self.doc.Name)

    def testMaterial(self):
        mesh = self.doc.addObject("Mesh::Feature", "Sphere")
        mesh.Mesh = Mesh.createBox(1.0, 1.0, 1.0)
        len1 = int(mesh.Mesh.CountFacets / 2)
        len2 = int(mesh.Mesh.CountFacets - len1)
        material = {"transparency": [0.2] * len1 + [0.8] * len2}
        material["binding"] = MeshEnums.Binding.PER_FACE
        material["ambientColor"] = [(1, 0, 0)] * (len1 + len2)
        material["diffuseColor"] = [(0, 1, 0)] * (len1 + len2)
        material["specularColor"] = [(0, 0, 1)] * (len1 + len2)
        material["emissiveColor"] = [(1, 1, 1)] * (len1 + len2)
        material["shininess"] = [0.3] * (len1 + len2)

        mesh.addProperty("Mesh::PropertyMaterial", "Material")
        mesh.Material = material

        TempPath = tempfile.gettempdir()
        SaveName = TempPath + os.sep + "mesh_with_material.FCStd"
        self.doc.saveAs(SaveName)
        FreeCAD.closeDocument(self.doc.Name)

        self.doc = FreeCAD.openDocument(SaveName)
        mesh2 = self.doc.Sphere
        material2 = mesh2.Material

        self.assertEqual(int(material2["binding"]), int(MeshEnums.Binding.PER_FACE))
        self.assertEqual(len(material2["ambientColor"]), len1 + len2)
        self.assertEqual(len(material2["diffuseColor"]), len1 + len2)
        self.assertEqual(len(material2["specularColor"]), len1 + len2)
        self.assertEqual(len(material2["emissiveColor"]), len1 + len2)
        self.assertEqual(len(material2["shininess"]), len1 + len2)
        self.assertEqual(len(material2["transparency"]), len1 + len2)


class Mesh3MFReadCases(unittest.TestCase):
    """3MF reading: root relationship, units, build items, colours and materials.

    The fixtures are written here from the 3MF Core Specification and Materials Extension (3MF Consortium).
    """

    CORE = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
    MATL = "http://schemas.microsoft.com/3dmanufacturing/material/2015/02"
    REL = "http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"
    # a right-angled tetrahedron: legs of length 1 along x, y and z
    VERTS = '<vertex x="0" y="0" z="0"/><vertex x="1" y="0" z="0"/><vertex x="0" y="1" z="0"/><vertex x="0" y="0" z="1"/>'

    def setUp(self):
        self.doc = FreeCAD.newDocument("Mesh3MF")
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        FreeCAD.closeDocument(self.doc.Name)

    def tris(self, extra=None):
        faces = [(0, 2, 1), (0, 1, 3), (0, 3, 2), (1, 2, 3)]
        extra = extra or [""] * 4
        return "".join('<triangle v1="%d" v2="%d" v3="%d" %s/>' % (a, b, c, e) for (a, b, c), e in zip(faces, extra))

    def obj(self, oid, name=None, attrs="", extra=None):
        n = ' name="%s"' % name if name else ""
        return '<object id="%d"%s %s><mesh><vertices>%s</vertices><triangles>%s</triangles></mesh></object>' % (
            oid, n, attrs, self.VERTS, self.tris(extra))

    def package(self, body, unit=None, part="3D/3dmodel.model", target=None, resources_prefix=""):
        import zipfile
        u = ' unit="%s"' % unit if unit else ""
        model = '<?xml version="1.0" encoding="UTF-8"?><model xmlns="%s" xmlns:m="%s"%s>%s</model>' % (self.CORE, self.MATL, u, body)
        path = os.path.join(self.tmp, "t%d.3mf" % len(os.listdir(self.tmp)))
        with zipfile.ZipFile(path, "w") as z:
            if target is not False:
                rel = '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="r0" Target="%s" Type="%s"/></Relationships>' % (target or "/" + part, self.REL)
                z.writestr("_rels/.rels", rel)
            z.writestr(part, model)
        return path

    def load(self, path):
        before = set(o.Name for o in self.doc.Objects)
        Mesh.insert(path, self.doc.Name)
        return [o for o in self.doc.Objects if o.Name not in before and o.isDerivedFrom("Mesh::Feature")]

    def grp(self):
        return FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Mod/Mesh")

    def test_start_part_through_relationship(self):
        path = self.package("<resources>%s</resources><build><item objectid=\"1\"/></build>" % self.obj(1), part="3D/other_name.part")
        objs = self.load(path)
        self.assertEqual(len(objs), 1)
        self.assertEqual(objs[0].Mesh.CountFacets, 4)

    def test_start_part_percent_encoded(self):
        path = self.package("<resources>%s</resources><build><item objectid=\"1\"/></build>" % self.obj(1), part="3D/a b.model", target="/3D/a%20b.model")
        self.assertEqual(len(self.load(path)), 1)

    def test_start_part_fixed_path_fallback(self):
        path = self.package("<resources>%s</resources><build><item objectid=\"1\"/></build>" % self.obj(1), target=False)
        self.assertEqual(len(self.load(path)), 1)

    def test_units_scaled_to_millimetre(self):
        want = {"millimeter": 1.0, "micron": 0.001, "centimeter": 10.0, "inch": 25.4, "foot": 304.8, "meter": 1000.0}
        for unit, f in want.items():
            path = self.package("<resources>%s</resources><build><item objectid=\"1\"/></build>" % self.obj(1), unit=unit)
            o = self.load(path)[0]
            self.assertAlmostEqual(o.Mesh.BoundBox.XLength, f, places=4, msg=unit)
        path = self.package("<resources>%s</resources><build><item objectid=\"1\"/></build>" % self.obj(1))
        self.assertAlmostEqual(self.load(path)[0].Mesh.BoundBox.XLength, 1.0, places=6)

    def test_unit_scales_item_translation(self):
        tr = "1 0 0 0 1 0 0 0 1 2 0 0"
        path = self.package("<resources>%s</resources><build><item objectid=\"1\" transform=\"%s\"/></build>" % (self.obj(1), tr), unit="centimeter")
        self.assertAlmostEqual(self.load(path)[0].Mesh.BoundBox.XMin, 20.0, places=4)

    def test_one_object_per_build_item(self):
        body = ("<resources>%s%s</resources><build><item objectid=\"1\"/>"
                "<item objectid=\"2\" transform=\"0 1 0 -1 0 0 0 0 1 10 0 0\"/>"
                "<item objectid=\"2\" transform=\"2 0 0 0 1 0 0 0 1 0 5 0\"/></build>") % (self.obj(1, "first"), self.obj(2, "second"))
        objs = self.load(self.package(body))
        self.assertEqual([o.Label for o in objs], ["first", "second", "second001"])
        self.assertTrue(objs[0].Placement.isIdentity())
        # a rigid transform becomes the Placement, a scaling one is baked into the vertices
        self.assertAlmostEqual(objs[1].Placement.Base.x, 10.0, places=6)
        self.assertAlmostEqual(objs[1].Placement.Rotation.Angle, math.pi / 2, places=6)
        self.assertTrue(objs[2].Placement.isIdentity())
        self.assertAlmostEqual(objs[2].Mesh.BoundBox.XLength, 2.0, places=5)
        self.assertAlmostEqual(objs[2].Mesh.BoundBox.YMin, 5.0, places=5)

    def test_mirroring_transform_is_baked_not_placement(self):
        body = ("<resources>%s</resources><build><item objectid=\"1\"/>"
                "<item objectid=\"1\" transform=\"-1 0 0 0 1 0 0 0 1 5 0 0\"/></build>") % self.obj(1, "m")
        objs = self.load(self.package(body))
        self.assertEqual(len(objs), 2)
        self.assertTrue(objs[1].Placement.isIdentity())
        self.assertAlmostEqual(objs[1].Mesh.BoundBox.XMin, 4.0, places=5)
        self.assertAlmostEqual(objs[1].Mesh.BoundBox.XMax, 5.0, places=5)

    def test_single_item_transform_baked(self):
        tr = "1 0 0 0 1 0 0 0 1 3 0 0"
        o = self.load(self.package("<resources>%s</resources><build><item objectid=\"1\" transform=\"%s\"/></build>" % (self.obj(1), tr)))[0]
        self.assertTrue(o.Placement.isIdentity())
        self.assertAlmostEqual(o.Mesh.BoundBox.XMin, 3.0, places=5)

    def test_object_not_in_build_is_left_out(self):
        objs = self.load(self.package("<resources>%s%s</resources><build><item objectid=\"1\"/></build>" % (self.obj(1), self.obj(2))))
        self.assertEqual(len(objs), 1)

    def test_components_flattened_with_transforms(self):
        comp = ('<object id="3"><components><component objectid="1"/>'
                '<component objectid="1" transform="1 0 0 0 1 0 0 0 1 10 0 0"/></components></object>')
        objs = self.load(self.package("<resources>%s%s</resources><build><item objectid=\"3\"/></build>" % (self.obj(1), comp)))
        self.assertEqual(len(objs), 1)
        self.assertEqual(objs[0].Mesh.CountFacets, 8)
        self.assertAlmostEqual(objs[0].Mesh.BoundBox.XMax, 11.0, places=5)

    def test_component_cycle_does_not_hang(self):
        cyc = ('<object id="3"><components><component objectid="4"/></components></object>'
               '<object id="4"><components><component objectid="3"/></components></object>')
        objs = self.load(self.package("<resources>%s%s</resources><build><item objectid=\"1\"/><item objectid=\"3\"/></build>" % (self.obj(1), cyc)))
        self.assertEqual(len(objs), 1)

    def colors(self, o):
        return [tuple(int(round(c * 255)) for c in col) for col in o.FaceColors]

    def test_colorgroup_to_face_colors(self):
        res = ('<m:colorgroup id="5"><m:color color="#FF0000"/><m:color color="#00FF0080"/></m:colorgroup>'
               + self.obj(1, "c", 'pid="5" pindex="0"', ["p1=\"1\"", "", "p1=\"1\" p2=\"0\" p3=\"0\"", ""]))
        o = self.load(self.package("<resources>%s</resources><build><item objectid=\"1\"/></build>" % res))[0]
        self.assertEqual(self.colors(o), [(0, 255, 0, 128), (255, 0, 0, 255), (0, 255, 0, 128), (255, 0, 0, 255)])
        self.assertFalse(hasattr(o, "Material3mf"))

    def test_object_level_colour_gives_full_list(self):
        res = '<m:colorgroup id="5"><m:color color="#102030"/></m:colorgroup>' + self.obj(1, "c", 'pid="5" pindex="0"')
        o = self.load(self.package("<resources>%s</resources><build><item objectid=\"1\"/></build>" % res))[0]
        self.assertEqual(self.colors(o), [(16, 32, 48, 255)] * 4)

    def test_basematerials_names_in_first_use_order(self):
        res = ('<basematerials id="2"><base name="steel" displaycolor="#808080FF"/><base name="gold" displaycolor="#FFD700FF"/>'
               '<base name="unused" displaycolor="#000000FF"/></basematerials>'
               + self.obj(1, "m", 'pid="2" pindex="1"', ["p1=\"0\"", "", "p1=\"0\"", ""]))
        o = self.load(self.package("<resources>%s</resources><build><item objectid=\"1\"/></build>" % res))[0]
        self.assertEqual(list(o.Material3mf), ["steel", "gold"])
        self.assertEqual(self.colors(o)[0], (128, 128, 128, 255))
        self.assertEqual(self.colors(o)[1], (255, 215, 0, 255))

    def test_nothing_to_carry(self):
        o = self.load(self.package("<resources>%s</resources><build><item objectid=\"1\"/></build>" % self.obj(1)))[0]
        self.assertFalse(hasattr(o, "FaceColors"))
        self.assertFalse(hasattr(o, "Material3mf"))

    def test_out_of_scope_property_gives_no_colour(self):
        res = ('<m:texture2dgroup id="9" texid="1"><m:tex2coord u="0" v="0"/></m:texture2dgroup>'
               + self.obj(1, "t", 'pid="9" pindex="0"'))
        o = self.load(self.package("<resources>%s</resources><build><item objectid=\"1\"/></build>" % res))[0]
        self.assertFalse(hasattr(o, "FaceColors"))

    def test_dangling_references_do_not_crash(self):
        res = ('<m:colorgroup id="5"><m:color color="nonsense"/></m:colorgroup>'
               + self.obj(1, "d", 'pid="77" pindex="0"', ["pid=\"5\" p1=\"0\"", "pid=\"5\" p1=\"9\"", "", ""]))
        o = self.load(self.package("<resources>%s</resources><build><item objectid=\"1\"/></build>" % res))[0]
        self.assertEqual(o.Mesh.CountFacets, 4)
        self.assertFalse(hasattr(o, "FaceColors"))

    def test_read_switch_off_gives_no_colours_but_keeps_fixes(self):
        res = '<m:colorgroup id="5"><m:color color="#FF0000"/></m:colorgroup>' + self.obj(1, "c", 'pid="5" pindex="0"')
        path = self.package("<resources>%s</resources><build><item objectid=\"1\"/></build>" % res, unit="centimeter", part="3D/x.part")
        g = self.grp()
        g.SetBool("Read3mfColors", False)
        try:
            o = self.load(path)[0]
        finally:
            g.RemBool("Read3mfColors")
        self.assertFalse(hasattr(o, "FaceColors"))
        self.assertAlmostEqual(o.Mesh.BoundBox.XLength, 10.0, places=4)

    def test_switch_is_not_written_by_import(self):
        path = self.package("<resources>%s</resources><build><item objectid=\"1\"/></build>" % self.obj(1))
        self.load(path)
        self.assertNotIn("Read3mfColors", [b for b in self.grp().GetBools()])

    def test_truncated_package_does_not_crash(self):
        path = self.package("<resources>%s</resources><build><item objectid=\"1\"/></build>" % self.obj(1))
        data = open(path, "rb").read()
        bad = os.path.join(self.tmp, "trunc.3mf")
        open(bad, "wb").write(data[: len(data) // 2])
        try:
            self.load(bad)
        except Exception:
            pass


class Mesh3MFWriteCases(unittest.TestCase):
    """3MF writing of colours and materials (3MF Materials and Properties Extension, 3MF Consortium)."""

    def setUp(self):
        self.doc = FreeCAD.newDocument("Mesh3MFW")
        self.tmp = tempfile.mkdtemp()
        self.grp = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Mod/Mesh")
        self.grp.RemBool("Write3mfColors")

    def tearDown(self):
        FreeCAD.closeDocument(self.doc.Name)
        self.grp.RemBool("Write3mfColors")

    def mesh_feature(self, colors, materials=None):
        f = self.doc.addObject("Mesh::Feature", "M")
        f.Mesh = Mesh.createBox(1, 1, 1)
        f.addProperty("App::PropertyColorList", "FaceColors")
        f.FaceColors = [colors[i % len(colors)] for i in range(f.Mesh.CountFacets)]
        if materials:
            f.addProperty("App::PropertyStringList", "Material3mf", "3MF")
            f.Material3mf = materials
        return f

    def model(self, path):
        import zipfile
        return zipfile.ZipFile(path).read("3D/3dmodel.model").decode()

    def reimport(self, path):
        d = FreeCAD.newDocument("Back")
        try:
            Mesh.insert(path, d.Name)
            o = [x for x in d.Objects if x.isDerivedFrom("Mesh::Feature")][0]
            return ([tuple(int(round(c * 255)) for c in col) for col in getattr(o, "FaceColors", [])],
                    list(getattr(o, "Material3mf", [])))
        finally:
            FreeCAD.closeDocument(d.Name)

    def test_face_colors_round_trip(self):
        cols = [(1.0, 0.0, 0.0, 1.0), (0.0, 0.0, 1.0, 1.0), (0.2, 0.9, 0.302, 0.5)]
        f = self.mesh_feature(cols)
        path = os.path.join(self.tmp, "c.3mf")
        Mesh.export([f], path)
        text = self.model(path)
        self.assertIn("colorgroup", text)
        self.assertIn('requiredextensions="m"', text)
        got, mats = self.reimport(path)
        want = [tuple(int(round(c * 255)) for c in cols[i % 3]) for i in range(f.Mesh.CountFacets)]
        self.assertEqual(got, want)
        self.assertEqual(mats, [])

    def test_single_colour_is_object_level(self):
        f = self.mesh_feature([(0.0, 1.0, 0.0, 1.0)])
        path = os.path.join(self.tmp, "o.3mf")
        Mesh.export([f], path)
        self.assertNotIn(" p1=", self.model(path))
        got, _ = self.reimport(path)
        self.assertEqual(set(got), {(0, 255, 0, 255)})

    def test_material_names_survive_as_basematerials(self):
        f = self.mesh_feature([(1.0, 0.0, 0.0, 1.0), (0.0, 0.0, 1.0, 1.0)], ["steel", "gold"])
        path = os.path.join(self.tmp, "b.3mf")
        Mesh.export([f], path)
        self.assertIn("<basematerials", self.model(path))
        _, mats = self.reimport(path)
        self.assertEqual(mats, ["steel", "gold"])

    def test_part_shape_material_written(self):
        b = self.doc.addObject("Part::Box", "Box")
        m = b.ShapeMaterial
        m.setAppearanceValue("DiffuseColor", "(1.0, 0.0, 0.0, 1.0)")
        b.ShapeMaterial = m
        self.doc.recompute()
        path = os.path.join(self.tmp, "p.3mf")
        Mesh.export([b], path)
        self.assertIn('displaycolor="#FF0000"', self.model(path))
        got, mats = self.reimport(path)
        self.assertEqual(set(got), {(255, 0, 0, 255)})
        self.assertEqual(mats, ["Box"])

    def test_no_colour_means_stock_bytes(self):
        f = self.doc.addObject("Mesh::Feature", "Plain")
        f.Mesh = Mesh.createBox(1, 1, 1)
        path = os.path.join(self.tmp, "n.3mf")
        Mesh.export([f], path)
        text = self.model(path)
        self.assertNotIn("colorgroup", text)
        self.assertNotIn("basematerials", text)
        self.assertNotIn("xmlns:m", text)

    def test_switch_off_writes_no_resource(self):
        f = self.mesh_feature([(1.0, 0.0, 0.0, 1.0), (0.0, 0.0, 1.0, 1.0)])
        self.grp.SetBool("Write3mfColors", False)
        path = os.path.join(self.tmp, "s.3mf")
        Mesh.export([f], path)
        text = self.model(path)
        self.assertNotIn("colorgroup", text)
        self.assertNotIn("pid=", text)

    def test_export_does_not_write_the_switch(self):
        f = self.mesh_feature([(1.0, 0.0, 0.0, 1.0)])
        Mesh.export([f], os.path.join(self.tmp, "k.3mf"))
        self.assertNotIn("Write3mfColors", list(self.grp.GetBools()))
