# SPDX-License-Identifier: LGPL-2.1-or-later

disable_occt8_deprecation_warnings()

add_subdirectory(App)
if(BUILD_GUI)
    add_subdirectory(Gui)
endif(BUILD_GUI)

set(Sketcher_Scripts
    Init.py
    SketcherExample.py
    TestSketcherApp.py
    Profiles.py
    SketcherDiagnosis.py
    SketcherAutoDefine.py
    SketcherBlocks.py
)

set(Sketcher_TestScripts
    SketcherTests/__init__.py
    SketcherTests/TestSketchFillet.py
    SketcherTests/TestSketcherSolver.py
    SketcherTests/TestSketchExpression.py
    SketcherTests/TestSketchValidateCoincidents.py
    SketcherTests/TestSketchCarbonCopyReverseMapping.py
    SketcherTests/TestSketchCarbonCopyReverseMapping.FCStd
    SketcherTests/TestSketchInternalFaces.py
    SketcherTests/TestSketcherEllipse.py
    SketcherTests/TestSketchDiagnosis.py
    SketcherTests/SketchDiagnosisCorpus.py
    SketcherTests/SketchBenchmark.py
    SketcherTests/TestSketchAutoDefine.py
    SketcherTests/SketchAutoDefineCorpus.py
    SketcherTests/SketchAutoDefineOracle.py
    SketcherTests/SketchAutoDefineRun.py
    SketcherTests/SketchAutoDefineMutants.py
    SketcherTests/TestSketchSolverCore.py
    SketcherTests/SketchSolverBaseline.py
    SketcherTests/SolverCoreMutants.py
    SketcherTests/SketchPlantedScale.py
    SketcherTests/TestSketchBlocks.py
    SketcherTests/SketchBlocksCorpus.py
    SketcherTests/SketchBlocksMutants.py
    SketcherTests/TestSketchBlockLibrary.py
    SketcherTests/TestSketchBlockCurves.py
    SketcherTests/SketchBlockCurvesCorpus.py
    SketcherTests/SketchBlockLibraryMutants.py
)

if(BUILD_GUI)
    list (APPEND Sketcher_Scripts
          InitGui.py
          TestSketcherGui.py
          TestSketcherDiagnosisPanelGui.py
          SketcherDiagnosisPanel.py
          CommandDiagnoseSketch.py
          TestSketcherFullyDefinePanelGui.py
          SketcherFullyDefinePanel.py
          CommandFullyDefine.py
          TestSketcherRelationsPanelGui.py
          SketcherRelationsPanel.py
          CommandRelations.py
          TestSketcherBlocksPanelGui.py
          SketcherBlocksPanel.py
          CommandBlocks.py
    )
    list (APPEND Sketcher_TestScripts
          SketcherTests/TestConstraintPreselectionGui.py
          SketcherTests/TestDistanceLabelExtensionGui.py
          SketcherTests/TestConstraintCommandsGui.py
          SketcherTests/TestCoincidentCommandGui.py
          SketcherTests/GuiTestCase.py
          SketcherTests/TestPlacementUpdate.py
          SketcherTests/TestOnViewParameterGui.py
          SketcherTests/TestExternalFacePreselection.py
          SketcherTests/TestVisualLayerListGui.py
          SketcherTests/TestSketcherDiagnosisPanelGui.py
          SketcherTests/SketchDiagnosisPanelMutants.py
          SketcherTests/TestSketcherFullyDefinePanelGui.py
          SketcherTests/SketchFullyDefinePanelMutants.py
          SketcherTests/diagnosis_gui_fixture.py
          SketcherTests/fully_define_gui_fixture.py
          SketcherTests/TestSketcherRelationsPanelGui.py
          SketcherTests/SketchRelationsPanelMutants.py
          SketcherTests/relations_gui_fixture.py
          SketcherTests/TestSketcherBlocksPanelGui.py
          SketcherTests/SketchBlocksPanelMutants.py
          SketcherTests/blocks_gui_fixture.py
    )
endif(BUILD_GUI)

set(Sketcher_Profiles
    ProfileLib/Hexagon.py
    ProfileLib/RegularPolygon.py
    ProfileLib/__init__.py
)

add_custom_target(SketcherScripts ALL
    SOURCES ${Sketcher_Scripts} ${Sketcher_Profiles} ${Sketcher_TestScripts}
)

fc_target_copy_resource(SketcherScripts
    ${CMAKE_CURRENT_SOURCE_DIR}
    ${CMAKE_BINARY_DIR}/Mod/Sketcher
    ${Sketcher_Scripts}
    ${Sketcher_Profiles}
    ${Sketcher_TestScripts}
)

INSTALL(
    FILES
        ${Sketcher_Scripts}
    DESTINATION
        Mod/Sketcher
)

INSTALL(
    FILES
        ${Sketcher_Profiles}
    DESTINATION
        Mod/Sketcher/ProfileLib
)

INSTALL(
    FILES
        ${Sketcher_TestScripts}
    DESTINATION
        Mod/Sketcher/SketcherTests
       )
