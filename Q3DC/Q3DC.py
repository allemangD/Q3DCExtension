import csv
from collections import defaultdict
import json
import logging
import math
import os
import time

import ctk
import numpy as np
import qt
import scipy.spatial
import vtk

import slicer
from slicer.ScriptedLoadableModule import *
from slicer.util import NodeModify

# needed for topological sort. Yes, this is basically just DFS.
try:
    import networkx as nx
except ModuleNotFoundError as e:
    # This requires a network connection!
    slicer.util.pip_install('networkx')
    import networkx as nx


#
# CalculateDisplacement
#

class Q3DC(ScriptedLoadableModule):
    def __init__(self, parent):
        ScriptedLoadableModule.__init__(self, parent)
        parent.title = "Q3DC "
        parent.categories = ["Quantification"]
        parent.dependencies = []
        parent.contributors = [
            'Lucie Macron (University of Michigan)',
            'Jean-Baptiste VIMORT (University of Michigan)',
            'James Hoctor (Kitware Inc)',
        ]
        parent.helpText = """
            """
        parent.acknowledgementText = """
    This work was supported by the National Institute of Dental
    & Craniofacial Research and the National Institute of Biomedical
    Imaging and Bioengineering under Award Number R01DE024450.
    The content is solely the responsibility of the authors and does
    not necessarily represent the official views of the National
    Institutes of Health.
    """
        self.parent = parent


class Q3DCWidget(ScriptedLoadableModuleWidget):

    def setup(self):
        print("-------Q3DC Widget Setup------")
        ScriptedLoadableModuleWidget.setup(self)
        # GLOBALS:
        self.interactionNode = slicer.mrmlScene.GetNodeByID("vtkMRMLInteractionNodeSingleton")
        self.computedDistanceList = list()
        self.computedAnglesList = list()
        self.computedLinePointList = list()
        self.renderer1 = None
        self.actor1 = None
        self.renderer2 = None
        self.actor2 = None
        self.renderer3 = None
        self.actor3 = None

        # Load widget from .ui file (created by Qt Designer)
        uiWidget = slicer.util.loadUI(self.resourcePath('UI/Q3DC.ui'))
        self.layout.addWidget(uiWidget)
        self.ui = slicer.util.childWidgetVariables(uiWidget)

        self.logic = Q3DCLogic(self.ui)
        self.logic.UpdateInterface = self.UpdateInterface

        #--------------------------- Scene --------------------------#
        self.SceneCollapsibleButton = self.ui.SceneCollapsibleButton # this attribute is usefull for Longitudinal quantification extension
        treeView = self.ui.treeView
        treeView.setMRMLScene(slicer.app.mrmlScene())
        treeView.sceneModel().setHorizontalHeaderLabels(["Models"])
        treeView.sortFilterProxyModel().nodeTypes = ['vtkMRMLModelNode','vtkMRMLMarkupsFiducialNode']
        treeView.header().setVisible(False)
        # --------------- landmark modification --------------
        self.inputModelLabel = self.ui.inputModelLabel  # this attribute is usefull for Longitudinal quantification extension
        self.inputLandmarksLabel = self.ui.inputLandmarksLabel  # this attribute is usefull for Longitudinal quantification extension
        self.ui.inputModelSelector.setMRMLScene(slicer.mrmlScene)
        self.ui.inputModelSelector.connect('currentNodeChanged(vtkMRMLNode*)', self.onModelChanged)
        self.ui.addLandmarkButton.connect('clicked()', self.onAddLandmarkButtonClicked)
        self.ui.inputLandmarksSelector.setMRMLScene(slicer.mrmlScene)
        self.ui.inputLandmarksSelector.setEnabled(False) # The "enable" property seems to not be imported from the .ui
        self.ui.inputLandmarksSelector.connect('currentNodeChanged(vtkMRMLNode*)', self.onLandmarksChanged)
        self.ui.landmarkComboBox.connect('currentIndexChanged(QString)', self.UpdateInterface)
        self.ui.surfaceDeplacementCheckBox.connect('stateChanged(int)', self.onSurfaceDeplacementStateChanged)

        # --------------- anatomical legend --------------
        self.suggested_landmarks = self.logic.load_suggested_landmarks(
            self.resourcePath('Data/base_fiducial_legend.csv'))
        self.anatomical_legend_space = self.ui.landmarkModifLayout
        self.anatomical_radio_buttons_layout = qt.QHBoxLayout()
        self.anatomical_legend_space.addLayout(self.anatomical_radio_buttons_layout)

        self.anatomical_legend = None
        self.init_anatomical_legend()
        self.anatomical_legend_view = slicer.qMRMLTableView()
        self.anatomical_legend_view.setMRMLTableNode(self.anatomical_legend)
        self.anatomical_legend_space.addWidget(self.anatomical_legend_view)
        self.anatomical_legend_view.show()
        self.anatomical_legend_view.setSelectionBehavior(
            qt.QAbstractItemView.SelectRows
        )
        self.anatomical_legend_view.connect('selectionChanged()', self.on_legend_row_selected)

        self.init_anatomical_radio_buttons()

        self.ui.legendFileButton.connect('clicked()', self.on_select_legend_file_clicked)

        #        ----------------- Compute Mid Point -------------
        self.ui.landmarkComboBox1.connect('currentIndexChanged(int)', self.UpdateInterface)
        self.ui.landmarkComboBox2.connect('currentIndexChanged(int)', self.UpdateInterface)
        self.ui.defineMiddlePointButton.connect('clicked()', self.onDefineMidPointClicked)
#        ------------------- 1st OPTION -------------------
        self.ui.fidListComboBoxA.setMRMLScene(slicer.mrmlScene)
        self.ui.fidListComboBoxB.setMRMLScene(slicer.mrmlScene)
        self.ui.computeDistancesPushButton.connect('clicked()', self.onComputeDistanceClicked)
        self.ui.landmarkComboBoxA.connect('currentIndexChanged(int)', self.UpdateInterface)
        self.ui.landmarkComboBoxB.connect('currentIndexChanged(int)', self.UpdateInterface)
        self.ui.fidListComboBoxA.connect('currentNodeChanged(vtkMRMLNode*)',
                                      lambda: self.logic.UpdateLandmarkComboboxA(self.ui.fidListComboBoxA, self.ui.landmarkComboBoxA))
        self.ui.fidListComboBoxB.connect('currentNodeChanged(vtkMRMLNode*)',
                                      lambda: self.logic.UpdateLandmarkComboboxA(self.ui.fidListComboBoxB, self.ui.landmarkComboBoxB))
        # ---------------------------- Directory - Export Button -----------------------------
        self.distanceTable = qt.QTableWidget()
        self.directoryExportDistance = ctk.ctkDirectoryButton()
        self.filenameExportDistance = qt.QLineEdit('distance.csv')
        self.exportDistanceButton = qt.QPushButton(" Export ")
        self.exportDistanceButton.enabled = True
        self.pathExportDistanceLayout = qt.QVBoxLayout()
        self.pathExportDistanceLayout.addWidget(self.directoryExportDistance)
        self.pathExportDistanceLayout.addWidget(self.filenameExportDistance)
        self.exportDistanceLayout = qt.QHBoxLayout()
        self.exportDistanceLayout.addLayout(self.pathExportDistanceLayout)
        self.exportDistanceLayout.addWidget(self.exportDistanceButton)
        self.tableAndExportLayout = qt.QVBoxLayout()
        self.tableAndExportLayout.addWidget(self.distanceTable)
        self.tableAndExportLayout.addLayout(self.exportDistanceLayout)
#       ------------------- 2nd OPTION -------------------
        self.ui.fidListComboBoxline1LA.setMRMLScene(slicer.mrmlScene)
        self.ui.fidListComboBoxline1LB.setMRMLScene(slicer.mrmlScene)
        self.ui.fidListComboBoxline2LA.setMRMLScene(slicer.mrmlScene)
        self.ui.fidListComboBoxline2LB.setMRMLScene(slicer.mrmlScene)
        self.ui.fidListComboBoxline1LA.connect('currentNodeChanged(vtkMRMLNode*)',
                                      lambda: self.logic.UpdateLandmarkComboboxA(self.ui.fidListComboBoxline1LA, self.ui.line1LAComboBox))
        self.ui.fidListComboBoxline1LB.connect('currentNodeChanged(vtkMRMLNode*)',
                                      lambda: self.logic.UpdateLandmarkComboboxA(self.ui.fidListComboBoxline1LB, self.ui.line1LBComboBox))
        self.ui.fidListComboBoxline2LA.connect('currentNodeChanged(vtkMRMLNode*)',
                                      lambda: self.logic.UpdateLandmarkComboboxA(self.ui.fidListComboBoxline2LA, self.ui.line2LAComboBox))
        self.ui.fidListComboBoxline2LB.connect('currentNodeChanged(vtkMRMLNode*)',
                                      lambda: self.logic.UpdateLandmarkComboboxA(self.ui.fidListComboBoxline2LB, self.ui.line2LBComboBox))
        self.ui.computeAnglesPushButton.connect('clicked()', self.onComputeAnglesClicked)
        self.ui.line1LAComboBox.connect('currentIndexChanged(int)', self.UpdateInterface)
        self.ui.line1LBComboBox.connect('currentIndexChanged(int)', self.UpdateInterface)
        self.ui.line2LAComboBox.connect('currentIndexChanged(int)', self.UpdateInterface)
        self.ui.line2LBComboBox.connect('currentIndexChanged(int)', self.UpdateInterface)
        self.ui.pitchCheckBox.connect('clicked(bool)', self.UpdateInterface)
        self.ui.rollCheckBox.connect('clicked(bool)', self.UpdateInterface)
        self.ui.yawCheckBox.connect('clicked(bool)', self.UpdateInterface)

        # ---------------------------- Directory - Export Button -----------------------------
        self.anglesTable = qt.QTableWidget()
        self.directoryExportAngle = ctk.ctkDirectoryButton()
        self.filenameExportAngle = qt.QLineEdit('angle.csv')
        self.exportAngleButton = qt.QPushButton("Export")
        self.exportAngleButton.enabled = True
        self.pathExportAngleLayout = qt.QVBoxLayout()
        self.pathExportAngleLayout.addWidget(self.directoryExportAngle)
        self.pathExportAngleLayout.addWidget(self.filenameExportAngle)
        self.exportAngleLayout = qt.QHBoxLayout()
        self.exportAngleLayout.addLayout(self.pathExportAngleLayout)
        self.exportAngleLayout.addWidget(self.exportAngleButton)
        self.tableAndExportAngleLayout = qt.QVBoxLayout()
        self.tableAndExportAngleLayout.addWidget(self.anglesTable)
        self.tableAndExportAngleLayout.addLayout(self.exportAngleLayout)
#       ------------------- 3rd OPTION -------------------
        self.ui.fidListComboBoxlineLA.setMRMLScene(slicer.mrmlScene)
        self.ui.fidListComboBoxlineLB.setMRMLScene(slicer.mrmlScene)
        self.ui.fidListComboBoxlinePoint.setMRMLScene(slicer.mrmlScene)
        self.ui.fidListComboBoxlineLA.connect('currentNodeChanged(vtkMRMLNode*)',
                                      lambda: self.logic.UpdateLandmarkComboboxA(self.ui.fidListComboBoxlineLA, self.ui.lineLAComboBox))
        self.ui.fidListComboBoxlineLB.connect('currentNodeChanged(vtkMRMLNode*)',
                                      lambda: self.logic.UpdateLandmarkComboboxA(self.ui.fidListComboBoxlineLB, self.ui.lineLBComboBox))
        self.ui.fidListComboBoxlinePoint.connect('currentNodeChanged(vtkMRMLNode*)',
                                      lambda: self.logic.UpdateLandmarkComboboxA(self.ui.fidListComboBoxlinePoint, self.ui.linePointComboBox))
        self.ui.computeLinePointPushButton.connect('clicked()', self.onComputeLinePointClicked)
        self.ui.lineLAComboBox.connect('currentIndexChanged(int)', self.UpdateInterface)
        self.ui.lineLBComboBox.connect('currentIndexChanged(int)', self.UpdateInterface)
        # ---------------------------- Directory - Export Button -----------------------------
        self.linePointTable = qt.QTableWidget()
        self.directoryExportLinePoint = ctk.ctkDirectoryButton()
        self.filenameExportLinePoint = qt.QLineEdit('linePoint.csv')
        self.exportLinePointButton = qt.QPushButton("Export")
        self.exportLinePointButton.enabled = True
        self.pathExportLinePointLayout = qt.QVBoxLayout()
        self.pathExportLinePointLayout.addWidget(self.directoryExportLinePoint)
        self.pathExportLinePointLayout.addWidget(self.filenameExportLinePoint)
        self.exportLinePointLayout = qt.QHBoxLayout()
        self.exportLinePointLayout.addLayout(self.pathExportLinePointLayout)
        self.exportLinePointLayout.addWidget(self.exportLinePointButton)
        self.tableAndExportLinePointLayout = qt.QVBoxLayout()
        self.tableAndExportLinePointLayout.addWidget(self.linePointTable)
        self.tableAndExportLinePointLayout.addLayout(self.exportLinePointLayout)
        # INITIALISATION:
        slicer.mrmlScene.AddObserver(slicer.mrmlScene.EndCloseEvent, self.onCloseScene)
        self.UpdateInterface()
        self.logic.initComboboxdict()

    def onCloseScene(self, obj, event):
        list = slicer.mrmlScene.GetNodesByClass("vtkMRMLModelNode")
        end = list.GetNumberOfItems()
        for i in range(0,end):
            model = list.GetItemAsObject(i)
            hardenModel = slicer.mrmlScene.GetNodesByName(model.GetName()).GetItemAsObject(0)
            slicer.mrmlScene.RemoveNode(hardenModel)
        if self.renderer1 :
            self.renderer1.RemoveActor(self.actor1)
        if self.renderer2 :
            self.renderer2.RemoveActor(self.actor2)
        if self.renderer3 :
            self.renderer3.RemoveActor(self.actor2)
        self.ui.landmarkComboBox1.clear()
        self.ui.landmarkComboBox.clear()
        self.ui.fidListComboBoxA.setCurrentNode(None)
        self.ui.fidListComboBoxB.setCurrentNode(None)
        self.ui.fidListComboBoxline1LA.setCurrentNode(None)
        self.ui.fidListComboBoxline1LB.setCurrentNode(None)
        self.ui.fidListComboBoxline2LA.setCurrentNode(None)
        self.ui.fidListComboBoxline2LB.setCurrentNode(None)
        self.ui.line1LAComboBox.clear()
        self.ui.line1LBComboBox.clear()
        self.ui.line2LAComboBox.clear()
        self.ui.line2LBComboBox.clear()
        self.ui.landmarkComboBox2.clear()
        self.ui.fidListComboBoxline2LB.setCurrentNode(None)
        self.ui.inputModelSelector.setCurrentNode(None)
        self.ui.inputLandmarksSelector.setCurrentNode(None)
        self.computedDistanceList = []
        self.computedAnglesList = []
        self.computedLinePointList = []
        self.linePointTable.clear()
        self.linePointTable.setRowCount(0)
        self.linePointTable.setColumnCount(0)
        self.anglesTable.clear()
        self.anglesTable.setRowCount(0)
        self.anglesTable.setColumnCount(0)
        self.distanceTable.clear()
        self.distanceTable.setRowCount(0)
        self.distanceTable.setColumnCount(0)

    def enter(self):
        print("enter Q3DC")
        model = self.ui.inputModelSelector.currentNode()
        fidlist = self.ui.inputLandmarksSelector.currentNode()

        if fidlist:
            if fidlist.GetAttribute("connectedModelID") != model.GetID():
                self.ui.inputModelSelector.setCurrentNode(None)
                self.ui.inputLandmarksSelector.setCurrentNode(None)
                self.ui.landmarkComboBox.clear()
        self.UpdateInterface()

        # Checking the names of the fiducials
        list = slicer.mrmlScene.GetNodesByClass("vtkMRMLMarkupsFiducialNode")
        end = list.GetNumberOfItems()
        for i in range(0,end):
            fidList = list.GetItemAsObject(i)
            landmarkDescription = self.logic.decodeJSON(fidList.GetAttribute("landmarkDescription"))
            if landmarkDescription:
                for n in range(fidList.GetNumberOfMarkups()):
                    markupID = fidList.GetNthMarkupID(n)
                    markupLabel = fidList.GetNthMarkupLabel(n)
                    landmarkDescription[markupID]["landmarkLabel"] = markupLabel
                fidList.SetAttribute("landmarkDescription",self.logic.encodeJSON(landmarkDescription))

    def UpdateInterface(self):
        self.ui.defineMiddlePointButton.enabled = self.ui.landmarkComboBox1.currentText != '' and \
                                               self.ui.landmarkComboBox2.currentText != '' and \
                                               self.ui.landmarkComboBox1.currentText != self.ui.landmarkComboBox2.currentText
        self.ui.computeDistancesPushButton.enabled = self.ui.landmarkComboBoxA.currentText != '' and\
                                                  self.ui.landmarkComboBoxB.currentText != '' and\
                                                  self.ui.landmarkComboBoxA.currentText != self.ui.landmarkComboBoxB.currentText
        self.ui.computeAnglesPushButton.enabled = self.ui.line1LAComboBox.currentText != '' and\
                                               self.ui.line1LBComboBox.currentText != '' and\
                                               self.ui.line2LAComboBox.currentText != '' and\
                                               self.ui.line2LBComboBox.currentText != '' and\
                                               self.ui.line1LAComboBox.currentText != self.ui.line1LBComboBox.currentText and\
                                               self.ui.line2LAComboBox.currentText != self.ui.line2LBComboBox.currentText and\
                                               (self.ui.pitchCheckBox.isChecked() or
                                                self.ui.rollCheckBox.isChecked() or
                                                self.ui.yawCheckBox.isChecked() )
        self.ui.computeLinePointPushButton.enabled = self.ui.lineLAComboBox.currentText != '' and\
                                                  self.ui.lineLBComboBox.currentText != '' and\
                                                  self.ui.linePointComboBox.currentText != '' and\
                                                  self.ui.lineLAComboBox.currentText != self.ui.lineLBComboBox.currentText

        # Clear Lines:
        if self.renderer1 :
            self.renderer1.RemoveActor(self.actor1)
            self.renderer1 = None
        if self.renderer2 :
            self.renderer2.RemoveActor(self.actor2)
            self.renderer2 = None
        if self.renderer3 :
            self.renderer3.RemoveActor(self.actor3)
            self.renderer3 = None
        if self.ui.line1LAComboBox.currentText != '' and\
                        self.ui.line1LBComboBox.currentText != '' and\
                        self.ui.line1LAComboBox.currentText != self.ui.line1LBComboBox.currentText :
            self.renderer1, self.actor1 = \
                self.logic.drawLineBetween2Landmark(self.ui.line1LAComboBox.currentText,
                                                    self.ui.line1LBComboBox.currentText,
                                                    self.ui.fidListComboBoxline1LA.currentNode(),
                                                    self.ui.fidListComboBoxline1LB.currentNode())
        if self.ui.line2LAComboBox.currentText != '' and\
                        self.ui.line2LBComboBox.currentText != '' and\
                        self.ui.line2LAComboBox.currentText != self.ui.line2LBComboBox.currentText :
            self.renderer2, self.actor2 = \
                self.logic.drawLineBetween2Landmark(self.ui.line2LAComboBox.currentText,
                                                    self.ui.line2LBComboBox.currentText,
                                                    self.ui.fidListComboBoxline2LA.currentNode(),
                                                    self.ui.fidListComboBoxline2LB.currentNode())
        if self.ui.lineLAComboBox.currentText != '' and\
                        self.ui.lineLBComboBox.currentText != '' and\
                        self.ui.lineLAComboBox.currentText != self.ui.lineLBComboBox.currentText:
            self.renderer3, self.actor3 = \
                self.logic.drawLineBetween2Landmark(self.ui.lineLAComboBox.currentText,
                                                    self.ui.lineLBComboBox.currentText,
                                                    self.ui.fidListComboBoxlineLA.currentNode(),
                                                    self.ui.fidListComboBoxlineLB.currentNode())
        self.logic.UpdateThreeDView(self.ui.landmarkComboBox.currentText)

    def init_anatomical_legend(self):
        if self.anatomical_legend is None:
            for table_node in slicer.mrmlScene.GetNodesByClass('vtkMRMLTableNode'):
                if table_node.GetAttribute('Q3DC.is_anatomical_legend') == 'True':
                    self.anatomical_legend = table_node
            if self.anatomical_legend is None:
                self.anatomical_legend = slicer.vtkMRMLTableNode()
                self.anatomical_legend.SetSaveWithScene(False)
                self.anatomical_legend.SetLocked(True)
                slicer.mrmlScene.AddNode(self.anatomical_legend)
                self.anatomical_legend.SetAttribute('Q3DC.is_anatomical_legend', 'True')

        al = self.anatomical_legend
        with NodeModify(al):
            al.RemoveAllColumns()
            al.AddColumn().SetName('Landmark')
            al.AddColumn().SetName('Description')
            al.SetUseColumnNameAsColumnHeader(True)

    def init_anatomical_radio_buttons(self):
        self.anatomical_radio_buttons = \
            [qt.QRadioButton(region) for region in self.suggested_landmarks.keys()]
        for i in range(self.anatomical_radio_buttons_layout.count()-1, -1, -1):
            self.anatomical_radio_buttons_layout.itemAt(i).widget().setParent(None)
        for radio_button in self.anatomical_radio_buttons:
            self.anatomical_radio_buttons_layout.addWidget(radio_button)
            radio_button.toggled.connect(
                lambda state, _radio_button=radio_button:
                    self.on_anatomical_radio_button_toggled(state, _radio_button)
            )
        self.anatomical_radio_buttons[0].toggle()

    def on_anatomical_radio_button_toggled(self, state, radio_button):
        if state:
            self.init_anatomical_legend()
            region = radio_button.text

            al = self.anatomical_legend
            with NodeModify(al):
                for landmark, description in self.suggested_landmarks[region]:
                    new_row_index = al.AddEmptyRow()
                    al.SetCellText(new_row_index, 0, landmark)
                    al.SetCellText(new_row_index, 1, description)
            self.anatomical_legend_view.resizeColumnsToContents()

    def on_legend_row_selected(self):
        # Calculate the index of the selected point.
        fidList = self.logic.selectedFidList
        if not fidList:
            return
        selectedFidReflID = self.logic.findIDFromLabel(
            fidList,
            self.ui.landmarkComboBox.currentText
        )
        if selectedFidReflID is None:
            # code would run correctly if we continued but wouldn't do anything
            return
        fid_index = fidList.GetNthControlPointIndexByID(selectedFidReflID)
        old_name = fidList.GetNthControlPointLabel(fid_index)

        # Look in the legend for the info from the selected row.
        selected_indices = self.anatomical_legend_view.selectedIndexes()
        if len(selected_indices) != 2:
            return
        name_index, description_index = selected_indices
        row_index = name_index.row()
        name = self.anatomical_legend.GetCellText(row_index, 0)
        description = self.anatomical_legend.GetCellText(row_index, 1)

        # Refuse to create multiple fiducials with the same name.
        for i in range(fidList.GetNumberOfControlPoints()):
            if name == fidList.GetNthControlPointLabel(i):
                return

        # Set the name and description of the selected point.
        fidList.SetNthControlPointLabel(fid_index, name)
        fidList.SetNthControlPointDescription(fid_index, description)

        # Update the landmark combo boxes to reflect the name change.
        self.logic.updateLandmarkComboBox(fidList, self.ui.landmarkComboBox, False)
        self.ui.landmarkComboBox.setCurrentText(name)
        for box in (self.ui.landmarkComboBox1, self.ui.landmarkComboBox2):
            new_selection = box.currentText
            if new_selection == old_name:
                new_selection = name
            self.logic.updateLandmarkComboBox(fidList, box)
            box.setCurrentText(new_selection)
        self.UpdateInterface()

    def on_select_legend_file_clicked(self):
        legend_filename = qt.QFileDialog.getOpenFileName(
            None,'Select File', '', 'CSV (*.csv)')
        if legend_filename == '':
            # User canceled the file selection dialog.
            return
        suggested_landmarks = self.logic.load_suggested_landmarks(
            legend_filename)
        if suggested_landmarks is None:
            return
        self.suggested_landmarks = suggested_landmarks
        self.init_anatomical_radio_buttons()

    def onModelChanged(self):
        print("-------Model Changed--------")
        if self.logic.selectedModel:
            Model = self.logic.selectedModel
            try:
                Model.RemoveObserver(self.logic.decodeJSON(self.logic.selectedModel.GetAttribute("modelModifieTagEvent")))
            except:
                pass
        self.logic.selectedModel = self.ui.inputModelSelector.currentNode()
        self.logic.ModelChanged(self.ui.inputModelSelector, self.ui.inputLandmarksSelector)
        self.ui.inputLandmarksSelector.setCurrentNode(None)

    def onLandmarksChanged(self):
        print("-------Landmarks Changed--------")
        if self.ui.inputModelSelector.currentNode():
            self.logic.FidList = self.ui.inputLandmarksSelector.currentNode()
            self.logic.selectedFidList = self.ui.inputLandmarksSelector.currentNode()
            self.logic.selectedModel = self.ui.inputModelSelector.currentNode()
            if self.ui.inputLandmarksSelector.currentNode():
                onSurface = self.ui.loadLandmarksOnSurfacCheckBox.isChecked()
                self.logic.connectLandmarks(self.ui.inputModelSelector,
                                      self.ui.inputLandmarksSelector,
                                      onSurface)
            else:
                self.ui.landmarkComboBox.clear()

    def onAddLandmarkButtonClicked(self):
        # Add fiducial on the scene.
        # If no input model selected, the addition of fiducial shouldn't be possible.
        selectionNode = slicer.mrmlScene.GetNodeByID("vtkMRMLSelectionNodeSingleton")
        selectionNode.SetReferenceActivePlaceNodeClassName("vtkMRMLMarkupsFiducialNode")
        if self.logic.selectedModel:
            if self.logic.selectedFidList:
                selectionNode.SetActivePlaceNodeID(self.logic.selectedFidList.GetID())
                self.interactionNode.SetCurrentInteractionMode(1)
            else:
                self.logic.warningMessage("Please select a fiducial list")
        else:
            self.logic.warningMessage("Please select a model")

    def onSurfaceDeplacementStateChanged(self):
        activeInput = self.logic.selectedModel
        if not activeInput:
            return
        fidList = self.logic.selectedFidList
        if not fidList:
            return
        selectedFidReflID = self.logic.findIDFromLabel(fidList, self.ui.landmarkComboBox.currentText)
        isOnSurface = self.ui.surfaceDeplacementCheckBox.isChecked()
        landmarkDescription = self.logic.decodeJSON(fidList.GetAttribute("landmarkDescription"))
        if isOnSurface:
            hardenModel = slicer.app.mrmlScene().GetNodeByID(fidList.GetAttribute("hardenModelID"))
            landmarkDescription[selectedFidReflID]["projection"]["isProjected"] = True
            landmarkDescription[selectedFidReflID]["projection"]["closestPointIndex"] =\
                self.logic.projectOnSurface(hardenModel, fidList, selectedFidReflID)
        else:
            landmarkDescription[selectedFidReflID]["projection"]["isProjected"] = False
            landmarkDescription[selectedFidReflID]["projection"]["closestPointIndex"] = None
            landmarkDescription[selectedFidReflID]["ROIradius"] = 0
        fidList.SetAttribute("landmarkDescription",self.logic.encodeJSON(landmarkDescription))

    def onDefineMidPointClicked(self):
        fidList = self.logic.selectedFidList
        if not fidList:
            self.logic.warningMessage("Please select a model of reference and a fiducial List.")
        label1 = self.ui.landmarkComboBox1.currentText
        label2 = self.ui.landmarkComboBox2.currentText
        landmark1ID = self.logic.findIDFromLabel(fidList, label1)
        landmark2ID = self.logic.findIDFromLabel(fidList, label2)
        coord = self.logic.calculateMidPointCoord(fidList, landmark1ID, landmark2ID)
        fidList.AddFiducial(coord[0],coord[1],coord[2], f'{label1}_{label2}')
        fidList.SetNthFiducialSelected(fidList.GetNumberOfMarkups() - 1, False)
        # update of the data structure
        landmarkDescription = self.logic.decodeJSON(fidList.GetAttribute("landmarkDescription"))
        numOfMarkups = fidList.GetNumberOfMarkups()
        markupID = fidList.GetNthMarkupID(numOfMarkups - 1)
        landmarkDescription[landmark1ID]["midPoint"]["definedByThisMarkup"].append(markupID)
        landmarkDescription[landmark2ID]["midPoint"]["definedByThisMarkup"].append(markupID)
        landmarkDescription[markupID]["midPoint"]["isMidPoint"] = True
        landmarkDescription[markupID]["midPoint"]["Point1"] = landmark1ID
        landmarkDescription[markupID]["midPoint"]["Point2"] = landmark2ID
        landmarkDescription[markupID]["projection"]["isProjected"] = False
        landmarkDescription[markupID]["projection"]["closestPointIndex"] = None

        if self.ui.midPointOnSurfaceCheckBox.isChecked():
            landmarkDescription[markupID]["projection"]["isProjected"] = True
            hardenModel = slicer.app.mrmlScene().GetNodeByID(fidList.GetAttribute("hardenModelID"))
            landmarkDescription[markupID]["projection"]["closestPointIndex"] = \
                self.logic.projectOnSurface(hardenModel, fidList, markupID)
        else:
            landmarkDescription[markupID]["projection"]["isProjected"] = False
        fidList.SetAttribute("landmarkDescription",self.logic.encodeJSON(landmarkDescription))
        self.logic.UpdateInterface()
        self.logic.updateLandmarkComboBox(fidList, self.ui.landmarkComboBox, False)
        fidList.SetNthFiducialPositionFromArray(numOfMarkups - 1, coord)

    def onComputeDistanceClicked(self):
        fidList = self.logic.selectedFidList
        fidListA = self.ui.fidListComboBoxA.currentNode()
        fidListB = self.ui.fidListComboBoxB.currentNode()
        nameList = [fidListA.GetName(), fidListB.GetName()]
        if not fidList:
            self.logic.warningMessage("Please connect a fiducial list to a model.")
            return
        for fidListIter in list(set(nameList)):
            landmarkDescription = slicer.mrmlScene.GetNodesByName(fidListIter).GetItemAsObject(0). \
                GetAttribute("landmarkDescription")
            if not landmarkDescription:
                self.logic.warningMessage(fidListIter + ' is not connected to a model. Please use "Add and Move '
                                                        'Landmarks" panel to connect the landmarks to a model.')
                return
        if self.computedDistanceList:
            self.exportDistanceButton.disconnect('clicked()', self.onExportButton)
            self.layout.removeWidget(self.distanceTable)
            self.layout.removeItem(self.tableAndExportLayout)
        self.computedDistanceList = self.logic.addOnDistanceList(self.computedDistanceList,
                                                                 self.ui.landmarkComboBoxA.currentText,
                                                                 self.ui.landmarkComboBoxB.currentText,
                                                                 fidListA,fidListB)
        self.distanceTable = self.logic.defineDistanceTable(self.distanceTable, self.computedDistanceList)
        self.ui.distanceLayout.addLayout(self.tableAndExportLayout)
        self.exportDistanceButton.connect('clicked()', self.onExportButton)

    def onExportButton(self):
        self.logic.exportationFunction(
            self.directoryExportDistance,
            self.filenameExportDistance,
            self.computedDistanceList,
            'distance'
        )

    def onComputeAnglesClicked(self):
        fidList = self.logic.selectedFidList
        fidListline1LA = self.ui.fidListComboBoxline1LA.currentNode()
        fidListline1LB = self.ui.fidListComboBoxline1LB.currentNode()
        fidListline2LA = self.ui.fidListComboBoxline2LA.currentNode()
        fidListline2LB = self.ui.fidListComboBoxline2LB.currentNode()
        nameList = [fidListline1LA.GetName(), fidListline1LB.GetName(), fidListline2LA.GetName(), fidListline2LB.GetName()]
        if not fidList:
            self.logic.warningMessage("Please connect a fiducial list to a model.")
            return
        for fidListIter in list(set(nameList)):
            landmarkDescription = slicer.mrmlScene.GetNodesByName(fidListIter).GetItemAsObject(0). \
                GetAttribute("landmarkDescription")
            if not landmarkDescription:
                self.logic.warningMessage(fidListIter + ' is not connected to a model. Please use "Add and Move '
                                                        'Landmarks" panel to connect the landmarks to a model.')
                return
        if self.computedAnglesList:
            self.exportAngleButton.disconnect('clicked()', self.onExportAngleButton)
            self.layout.removeWidget(self.anglesTable)
            self.layout.removeItem(self.tableAndExportAngleLayout)
        self.computedAnglesList = self.logic.addOnAngleList(self.computedAnglesList,
                                                            self.ui.line1LAComboBox.currentText,
                                                            self.ui.line1LBComboBox.currentText,
                                                            self.ui.fidListComboBoxline1LA.currentNode(),
                                                            self.ui.fidListComboBoxline1LB.currentNode(),
                                                            self.ui.line2LAComboBox.currentText,
                                                            self.ui.line2LBComboBox.currentText,
                                                            self.ui.fidListComboBoxline2LA.currentNode(),
                                                            self.ui.fidListComboBoxline2LB.currentNode(),
                                                            self.ui.pitchCheckBox.isChecked(),
                                                            self.ui.yawCheckBox.isChecked(),
                                                            self.ui.rollCheckBox.isChecked()
                                                            )
        self.anglesTable = self.logic.defineAnglesTable(self.anglesTable, self.computedAnglesList)
        self.ui.angleLayout.addLayout(self.tableAndExportAngleLayout)
        self.exportAngleButton.connect('clicked()', self.onExportAngleButton)

    def onExportAngleButton(self):
        self.logic.exportationFunction(
            self.directoryExportAngle,
            self.filenameExportAngle,
            self.computedAnglesList,
            'angle'
        )

    def onComputeLinePointClicked(self):
        fidList = self.logic.selectedFidList
        if not fidList:
            self.logic.warningMessage("Please connect a fiducial list to a model.")
            return
        fidListlineLA = self.ui.fidListComboBoxlineLA.currentNode()
        fidListlineLB = self.ui.fidListComboBoxlineLB.currentNode()
        fidListPoint = self.ui.fidListComboBoxlinePoint.currentNode()
        nameList = [fidListlineLA.GetName(), fidListlineLB.GetName(), fidListPoint.GetName()]
        for fidListIter in list(set(nameList)):
            landmarkDescription = slicer.mrmlScene.GetNodesByName(fidListIter).GetItemAsObject(0). \
                GetAttribute("landmarkDescription")
            if not landmarkDescription:
                self.logic.warningMessage(fidListIter + ' is not connected to a model. Please use "Add and Move '
                                                        'Landmarks" panel to connect the landmarks to a model.')
                return
        if self.computedLinePointList:
            self.exportLinePointButton.disconnect('clicked()', self.onExportLinePointButton)
            self.layout.removeWidget(self.linePointTable)
            self.layout.removeItem(self.tableAndExportLinePointLayout)
        self.computedLinePointList = self.logic.addOnLinePointList(self.computedLinePointList,
                                                           self.ui.lineLAComboBox.currentText,
                                                           self.ui.lineLBComboBox.currentText,
                                                                   fidListlineLA,
                                                                   fidListlineLB,
                                                           self.ui.linePointComboBox.currentText,
                                                                   fidListPoint,
                                                           )
        self.linePointTable = self.logic.defineDistanceLinePointTable(self.linePointTable, self.computedLinePointList)
        self.ui.LinePointLayout.addLayout(self.tableAndExportLinePointLayout)
        self.exportLinePointButton.connect('clicked()', self.onExportLinePointButton)

    def onExportLinePointButton(self):
        self.logic.exportationFunction(
            self.directoryExportLinePoint,
            self.filenameExportLinePoint,
            self.computedLinePointList,
            'linePoint'
        )


class Q3DCLogic(ScriptedLoadableModuleLogic):
    def __init__(self, interface):
        self.interface = interface
        self.selectedModel = None
        self.selectedFidList = None
        self.numberOfDecimals = 3
        system = qt.QLocale().system()
        self.decimalPoint = chr(system.decimalPoint())
        self.comboboxdict = dict()

    @staticmethod
    def load_suggested_landmarks(filepath):
        suggested_landmarks = defaultdict(list)
        try:
            with open(filepath, newline='', encoding='utf8') as suggestions_file:
                reader = csv.DictReader(suggestions_file)
                for row in reader:
                    region = row['Region'].title()
                    landmark = row['Landmark']
                    name = row['Name']
                    suggested_landmarks[region].append((landmark, name))
            return suggested_landmarks
        except OSError as e:
            slicer.util.delayDisplay('Unable to find/open file.')
            logging.info('User attempted to open a landmark legend file.\n' + repr(e))
            return None
        except csv.Error as e:
            slicer.util.delayDisplay('The selected file is not formatted properly.')
            logging.info('User attempted to open a landmark legend file.\n' + repr(e))
            return None
        except KeyError as e:
            slicer.util.delayDisplay('The selected file does not have the right column names.')
            logging.info('User attempted to open a landmark legend file.\n' + repr(e))
            return None

    def initComboboxdict(self):
        self.comboboxdict[self.interface.landmarkComboBoxA] = None
        self.comboboxdict[self.interface.landmarkComboBoxB] = None
        self.comboboxdict[self.interface.line1LAComboBox] = None
        self.comboboxdict[self.interface.line1LBComboBox] = None
        self.comboboxdict[self.interface.line2LAComboBox] = None
        self.comboboxdict[self.interface.line2LBComboBox] = None
        self.comboboxdict[self.interface.lineLAComboBox] = None
        self.comboboxdict[self.interface.lineLBComboBox] = None
        self.comboboxdict[self.interface.linePointComboBox] = None

    class distanceValuesStorage(object):
        def __init__(self):
            self.startLandmarkID = None
            self.endLandmarkID = None
            self.startLandmarkName = None
            self.endLandmarkName = None
            self.RLComponent = None
            self.APComponent = None
            self.SIComponent = None
            self.ThreeDComponent = None

    class angleValuesStorage(object):
        def __init__(self):
            self.landmarkALine1ID = None
            self.landmarkBLine1ID = None
            self.landmarkALine2ID = None
            self.landmarkBLine2ID = None
            self.landmarkALine1Name = None
            self.landmarkBLine1Name = None
            self.landmarkALine2Name = None
            self.landmarkBLine2Name = None
            self.Pitch = None
            self.Roll = None
            self.Yaw = None

    class distanceLinePointStorage(object):
        def __init__(self):
            self.landmarkALineID = None
            self.landmarkBLineID = None
            self.landmarkPointID = None
            self.landmarkALineName = None
            self.landmarkBLineName = None
            self.landmarkPointName = None
            self.RLComponent = None
            self.APComponent = None
            self.SIComponent = None
            self.ThreeDComponent = None

    def UpdateThreeDView(self, landmarkLabel):
        # Update the 3D view on Slicer
        if not self.selectedFidList:
            return
        if not self.selectedModel:
            return
        print("UpdateThreeDView")
        active = self.selectedFidList
        #deactivate all landmarks
        list = slicer.mrmlScene.GetNodesByClass("vtkMRMLMarkupsFiducialNode")
        end = list.GetNumberOfItems()
        selectedFidReflID = self.findIDFromLabel(active,landmarkLabel)
        for i in range(0,end):
            fidList = list.GetItemAsObject(i)
            print(fidList.GetID())
            landmarkDescription = self.decodeJSON(fidList.GetAttribute("landmarkDescription"))
            if landmarkDescription:
                for key in landmarkDescription.keys():
                    markupsIndex = fidList.GetNthControlPointIndexByID(key)
                    if key != selectedFidReflID:
                        fidList.SetNthMarkupLocked(markupsIndex, True)
                    else:
                        fidList.SetNthMarkupLocked(markupsIndex, False)
                        fidList.SetNthMarkupLocked(markupsIndex, False)
        displayNode = self.selectedModel.GetModelDisplayNode()
        displayNode.SetScalarVisibility(False)
        if selectedFidReflID != False:
            displayNode.SetScalarVisibility(True)

    def createIntermediateHardenModel(self, model):
        hardenModel = slicer.mrmlScene.GetNodesByName("SurfaceRegistration_" + model.GetName() + "_hardenCopy_" + str(
            slicer.app.applicationPid())).GetItemAsObject(0)
        if hardenModel is None:
            hardenModel = slicer.vtkMRMLModelNode()
        hardenPolyData = vtk.vtkPolyData()
        hardenPolyData.DeepCopy(model.GetPolyData())
        hardenModel.SetAndObservePolyData(hardenPolyData)
        hardenModel.SetName(
            "SurfaceRegistration_" + model.GetName() + "_hardenCopy_" + str(slicer.app.applicationPid()))
        if model.GetParentTransformNode():
            hardenModel.SetAndObserveTransformNodeID(model.GetParentTransformNode().GetID())
        hardenModel.HideFromEditorsOn()
        slicer.mrmlScene.AddNode(hardenModel)
        logic = slicer.vtkSlicerTransformLogic()
        logic.hardenTransform(hardenModel)
        return hardenModel

    def onModelModified(self, obj, event):
        #recompute the harden model
        hardenModel = self.createIntermediateHardenModel(obj)
        obj.SetAttribute("hardenModelID",hardenModel.GetID())
        # for each fiducial list
        list = slicer.mrmlScene.GetNodesByClass("vtkMRMLMarkupsFiducialNode")
        end = list.GetNumberOfItems()
        for i in range(0,end):
            # If landmarks are projected on the modified model
            fidList = list.GetItemAsObject(i)
            if fidList.GetAttribute("connectedModelID"):
                if fidList.GetAttribute("connectedModelID") == obj.GetID():
                    #replace the harden model with the new one
                    fidList.SetAttribute("hardenModelID",hardenModel.GetID())
                    #reproject the fiducials on the new model
                    landmarkDescription = self.decodeJSON(fidList.GetAttribute("landmarkDescription"))
                    for n in range(fidList.GetNumberOfMarkups()):
                        markupID = fidList.GetNthMarkupID(n)
                        if landmarkDescription[markupID]["projection"]["isProjected"] == True:
                            hardenModel = slicer.app.mrmlScene().GetNodeByID(fidList.GetAttribute("hardenModelID"))
                            markupsIndex = fidList.GetNthControlPointIndexByID(markupID)
                            self.replaceLandmark(hardenModel.GetPolyData(), fidList, markupsIndex,
                                                 landmarkDescription[markupID]["projection"]["closestPointIndex"])
                        fidList.SetAttribute("landmarkDescription",self.encodeJSON(landmarkDescription))

    def ModelChanged(self, inputModelSelector, inputLandmarksSelector):
        inputModel = inputModelSelector.currentNode()
        # if a Model Node is present
        if inputModel:
            self.selectedModel = inputModel
            hardenModel = self.createIntermediateHardenModel(inputModel)
            inputModel.SetAttribute("hardenModelID",hardenModel.GetID())
            modelModifieTagEvent = inputModel.AddObserver(inputModel.TransformModifiedEvent, self.onModelModified)
            inputModel.SetAttribute("modelModifieTagEvent",self.encodeJSON({'modelModifieTagEvent':modelModifieTagEvent}))
            inputLandmarksSelector.setEnabled(True)
        # if no model is selected
        else:
            # Update the fiducial list selector
            inputLandmarksSelector.setCurrentNode(None)
            inputLandmarksSelector.setEnabled(False)

    def isUnderTransform(self, markups):
        if markups.GetParentTransformNode():
            messageBox = ctk.ctkMessageBox()
            messageBox.setWindowTitle(" /!\ WARNING /!\ ")
            messageBox.setIcon(messageBox.Warning)
            messageBox.setText("Your Markup Fiducial Node is currently modified by a transform,"
                               "if you choose to continue the program will apply the transform"
                               "before doing anything else!")
            messageBox.setInformativeText("Do you want to continue?")
            messageBox.setStandardButtons(messageBox.No | messageBox.Yes)
            choice = messageBox.exec_()
            if choice == messageBox.Yes:
                logic = slicer.vtkSlicerTransformLogic()
                logic.hardenTransform(markups)
                return False
            else:
                messageBox.setText(" Node not modified")
                messageBox.setStandardButtons(messageBox.Ok)
                messageBox.setInformativeText("")
                messageBox.exec_()
                return True
        else:
            return False

    def connectedModelChangement(self):
        messageBox = ctk.ctkMessageBox()
        messageBox.setWindowTitle(" /!\ WARNING /!\ ")
        messageBox.setIcon(messageBox.Warning)
        messageBox.setText("The Markup Fiducial Node selected is curently projected on an"
                           "other model, if you chose to continue the fiducials will be  "
                           "reprojected, and this could impact the functioning of other modules")
        messageBox.setInformativeText("Do you want to continue?")
        messageBox.setStandardButtons(messageBox.No | messageBox.Yes)
        choice = messageBox.exec_()
        if choice == messageBox.Yes:
            return True
        else:
            messageBox.setText(" Node not modified")
            messageBox.setStandardButtons(messageBox.Ok)
            messageBox.setInformativeText("")
            messageBox.exec_()
            return False

    @staticmethod
    def recover_midpoint_provenance(landmarks):
        '''
        When a new list of fiducials is loaded from a file, we know which are
        midpoints, but we don't know from which points those midpoints were
        constructed. This function recovers this information.
        '''
        # Build the data structures we will need.
        point_ids = []
        points = []
        ids_and_midpoints = []
        all_ids = []
        scratch_array = np.zeros(3)
        for n in range(landmarks.GetNumberOfMarkups()):
            markupID = landmarks.GetNthMarkupID(n)
            is_sel = landmarks.GetNthFiducialSelected(n)
            landmarks.GetNthFiducialPosition(n, scratch_array)
            markup_pos = np.copy(scratch_array)
            if is_sel:  # not a midpoint
                point_ids.append(markupID)
                points.append(markup_pos)
            else:       # midpoint
                ids_and_midpoints.append((markupID, markup_pos))
            all_ids.append(markupID)

        # This is the structure we want to populate to help build
        # landmarkDescription in createNewDataStructure.
        midpoint_data = {
                point_id: {
                    'definedByThisMarkup': [],
                    'isMidPoint': False,
                    'Point1': None,
                    'Point2': None,
                } for point_id in all_ids
            }

        # Use a kd-tree to find points that could be the missing endpoint of a
        # hypothetical midpoint operation.
        points = np.array(points)
        n_new_points = len(points)
        while n_new_points > 0 and len(ids_and_midpoints) > 0:
            kdt = scipy.spatial.KDTree(points)
            n_new_points = 0
            new_ids_and_midpoints = []
            for mp_id, mp in ids_and_midpoints:
                provenance_found = False
                for p_idx, p in enumerate(points):
                    # hp for "hypothetical point"
                    # mp = (hp + p) / 2
                    hp = 2*mp - p
                    max_error = np.linalg.norm(mp - p) / 10000.0
                    distance, kdt_p_idx = kdt.query(
                            hp, distance_upper_bound=max_error)
                    # distance = np.inf on failure
                    if distance < max_error:
                        ids = (point_ids[p_idx], point_ids[kdt_p_idx])
                        midpoint_data[mp_id].update({
                                'isMidPoint': True,
                                'Point1': ids[0],
                                'Point2': ids[1],
                            })
                        for id_ in ids:
                            midpoint_data[id_]['definedByThisMarkup'].append(mp_id)

                        provenance_found = True
                        point_ids.append(mp_id)
                        points = np.concatenate((points, mp.reshape((1, 3))))
                        n_new_points += 1
                        break
                if not provenance_found:
                    new_ids_and_midpoints.append((mp_id, mp))
            ids_and_midpoints = new_ids_and_midpoints

        return midpoint_data

    def createNewDataStructure(self, landmarks, model, onSurface):
        landmarks.SetAttribute("connectedModelID",model.GetID())
        landmarks.SetAttribute("hardenModelID",model.GetAttribute("hardenModelID"))
        landmarkDescription = dict()

        midpoint_data = self.recover_midpoint_provenance(landmarks)
        for n in range(landmarks.GetNumberOfMarkups()):
            markupID = landmarks.GetNthMarkupID(n)
            landmarkDescription[markupID] = {'midPoint': midpoint_data[markupID]}

        for n in range(landmarks.GetNumberOfMarkups()):
            markupID = landmarks.GetNthMarkupID(n)
            landmarkLabel = landmarks.GetNthMarkupLabel(n)
            landmarkDescription[markupID]["landmarkLabel"] = landmarkLabel
            landmarkDescription[markupID]["ROIradius"] = 0
            landmarkDescription[markupID]["projection"] = dict()
            if onSurface and not landmarkDescription[markupID]['midPoint']['isMidPoint']:
                landmarkDescription[markupID]["projection"]["isProjected"] = True
                hardenModel = slicer.app.mrmlScene().GetNodeByID(landmarks.GetAttribute("hardenModelID"))
                landmarkDescription[markupID]["projection"]["closestPointIndex"] = \
                    self.projectOnSurface(hardenModel, landmarks, markupID)
            else:
                landmarkDescription[markupID]["projection"]["isProjected"] = False
                landmarkDescription[markupID]["projection"]["closestPointIndex"] = None

        if onSurface:
            for n in range(landmarks.GetNumberOfMarkups()):
                markupID = landmarks.GetNthMarkupID(n)
                nth_midpoint_data = landmarkDescription[markupID]['midPoint']
                if nth_midpoint_data['isMidPoint']:
                    parent_id1 = nth_midpoint_data['Point1']
                    parent_id2 = nth_midpoint_data['Point2']
                    coord = self.calculateMidPointCoord(landmarks, parent_id1, parent_id2)
                    index = landmarks.GetNthControlPointIndexByID(markupID)
                    landmarks.SetNthFiducialPositionFromArray(index, coord)

        landmarks.SetAttribute("landmarkDescription",self.encodeJSON(landmarkDescription))
        planeDescription = dict()
        landmarks.SetAttribute("planeDescription",self.encodeJSON(planeDescription))
        landmarks.SetAttribute("isClean",self.encodeJSON({"isClean":False}))
        landmarks.SetAttribute("lastTransformID",None)
        landmarks.SetAttribute("arrayName",model.GetName() + "_ROI")

        self.conform_selectedness_to_midpoint_status(landmarks)

    def conform_selectedness_to_midpoint_status(self, landmarks):
        landmarkDescription = self.decodeJSON(landmarks.GetAttribute("landmarkDescription"))
        for n in range(landmarks.GetNumberOfMarkups()):
            markupID = landmarks.GetNthMarkupID(n)
            isMidPoint = landmarkDescription[markupID]['midPoint']['isMidPoint']
            landmarks.SetNthFiducialSelected(n, not isMidPoint)

    def changementOfConnectedModel(self, landmarks, model, onSurface):
        landmarks.SetAttribute("connectedModelID", model.GetID())
        landmarks.SetAttribute("hardenModelID", model.GetAttribute("hardenModelID"))
        landmarkDescription = self.decodeJSON(landmarks.GetAttribute("landmarkDescription"))

        D = nx.DiGraph()
        for n in range(landmarks.GetNumberOfMarkups()):
            markupID = landmarks.GetNthMarkupID(n)
            D.add_node(markupID)
            dbtm = landmarkDescription[markupID]['midPoint']['definedByThisMarkup']
            for dependent_point in dbtm:
                D.add_edge(markupID, dependent_point)

        for markupID in nx.topological_sort(D):
            if onSurface:
                if landmarkDescription[markupID]["projection"]["isProjected"] == True:
                    hardenModel = slicer.app.mrmlScene().GetNodeByID(landmarks.GetAttribute("hardenModelID"))
                    landmarkDescription[markupID]["projection"]["closestPointIndex"] = \
                        self.projectOnSurface(hardenModel, landmarks, markupID)
                elif landmarkDescription[markupID]['midPoint']['isMidPoint']:
                    parent_id1 = landmarkDescription[markupID]['midPoint']['Point1']
                    parent_id2 = landmarkDescription[markupID]['midPoint']['Point2']
                    coord = self.calculateMidPointCoord(landmarks, parent_id1, parent_id2)
                    index = landmarks.GetNthControlPointIndexByID(markupID)
                    landmarks.SetNthFiducialPositionFromArray(index, coord)
            else:
                landmarkDescription[markupID]["projection"]["isProjected"] = False
                landmarkDescription[markupID]["projection"]["closestPointIndex"] = None

        landmarks.SetAttribute("landmarkDescription", self.encodeJSON(landmarkDescription))
        landmarks.SetAttribute("isClean",self.encodeJSON({"isClean":False}))

    def connectLandmarks(self, modelSelector, landmarkSelector, onSurface):
        model = modelSelector.currentNode()
        landmarks = landmarkSelector.currentNode()
        self.selectedFidList = landmarks
        self.selectedModel = model
        if not (model and landmarks):
            return

        if self.isUnderTransform(landmarks):
            landmarkSelector.setCurrentNode(None)
            return
        connectedModelID = landmarks.GetAttribute("connectedModelID")
        try:
            tag = self.decodeJSON(landmarks.GetAttribute("PointAddedEventTag"))
            landmarks.RemoveObserver(tag["PointAddedEventTag"])
            print("adding observers removed!")
        except:
            pass
        try:
            tag = self.decodeJSON(landmarks.GetAttribute("UpdatesLinesEventTag"))
            landmarks.RemoveObserver(tag["UpdatesLinesEventTag"])
            print("lines observers removed!")
        except:
            pass
        try:
            tag = self.decodeJSON(landmarks.GetAttribute("PointModifiedEventTag"))
            landmarks.RemoveObserver(tag["PointModifiedEventTag"])
            print("moving observers removed!")
        except:
            pass
        try:
            tag = self.decodeJSON(landmarks.GetAttribute("PointRemovedEventTag"))
            landmarks.RemoveObserver(tag["PointRemovedEventTag"])
            print("removing observers removed!")
        except:
            pass
        if connectedModelID:
            if connectedModelID != model.GetID():
                if self.connectedModelChangement():
                    self.changementOfConnectedModel(landmarks, model, onSurface)
                else:
                    landmarkSelector.setCurrentNode(None)
                    return
            else:
                landmarks.SetAttribute("hardenModelID",model.GetAttribute("hardenModelID"))
        # creation of the data structure
        else:
            self.createNewDataStructure(landmarks, model, onSurface)
        #update of the landmark Combo Box
        self.updateLandmarkComboBox(landmarks, self.interface.landmarkComboBox, False)
        self.updateLandmarkComboBox(landmarks, self.interface.landmarkComboBox1)
        self.updateLandmarkComboBox(landmarks, self.interface.landmarkComboBox2)
        #adding of listeners
        PointAddedEventTag = landmarks.AddObserver(landmarks.PointAddedEvent, self.onPointAddedEvent)
        landmarks.SetAttribute("PointAddedEventTag",self.encodeJSON({"PointAddedEventTag":PointAddedEventTag}))
        UpdatesLinesEventTag = landmarks.AddObserver(landmarks.PointModifiedEvent, self.updateLinesEvent)
        landmarks.SetAttribute("UpdatesLinesEventTag",self.encodeJSON({"UpdatesLinesEventTag":UpdatesLinesEventTag}))
        PointModifiedEventTag = landmarks.AddObserver(landmarks.PointModifiedEvent, self.onPointModifiedEvent)
        landmarks.SetAttribute("PointModifiedEventTag",self.encodeJSON({"PointModifiedEventTag":PointModifiedEventTag}))
        PointRemovedEventTag = landmarks.AddObserver(landmarks.PointRemovedEvent, self.onPointRemovedEvent)
        landmarks.SetAttribute("PointRemovedEventTag",self.encodeJSON({"PointRemovedEventTag":PointRemovedEventTag}))

    # Called when a landmark is added on a model
    def onPointAddedEvent(self, obj, event):
        print("------markup adding-------")
        landmarkDescription = self.decodeJSON(obj.GetAttribute("landmarkDescription"))
        numOfMarkups = obj.GetNumberOfMarkups()
        markupID = obj.GetNthMarkupID(numOfMarkups - 1)
        landmarkDescription[markupID] = dict()
        landmarkLabel = obj.GetNthMarkupLabel(numOfMarkups - 1)
        landmarkDescription[markupID]["landmarkLabel"] = landmarkLabel
        landmarkDescription[markupID]["ROIradius"] = 0
        landmarkDescription[markupID]["projection"] = dict()
        landmarkDescription[markupID]["projection"]["isProjected"] = True
        # The landmark will be projected by onPointModifiedEvent
        landmarkDescription[markupID]["midPoint"] = dict()
        landmarkDescription[markupID]["midPoint"]["definedByThisMarkup"] = list()
        landmarkDescription[markupID]["midPoint"]["isMidPoint"] = False
        landmarkDescription[markupID]["midPoint"]["Point1"] = None
        landmarkDescription[markupID]["midPoint"]["Point2"] = None
        obj.SetAttribute("landmarkDescription",self.encodeJSON(landmarkDescription))
        self.updateAllLandmarkComboBox(obj, markupID)
        self.UpdateInterface()
        qt.QTimer.singleShot(0, lambda : self.onPointModifiedEvent(obj,None))

    def updateLinesEvent(self, obj, event):
        if self.interface.line1LAComboBox.currentText != '' and self.interface.line1LBComboBox.currentText != '' \
                and self.interface.line1LAComboBox.currentText != self.interface.line1LBComboBox.currentText :
            # Clear Lines, then define new ones
            if self.interface.renderer1 :
                self.interface.renderer1.RemoveActor(self.interface.actor1)
            self.interface.renderer1, self.interface.actor1 = \
                self.drawLineBetween2Landmark(self.interface.line1LAComboBox.currentText,
                                              self.interface.line1LBComboBox.currentText,
                                              self.interface.fidListComboBoxline1LA.currentNode(),
                                              self.interface.fidListComboBoxline1LB.currentNode())
        if self.interface.line2LAComboBox.currentText != '' and self.interface.line2LBComboBox.currentText != '' \
                and self.interface.line2LAComboBox.currentText != self.interface.line2LBComboBox.currentText :
            if self.interface.renderer2 :
                self.interface.renderer2.RemoveActor(self.interface.actor2)
            self.interface.renderer2, self.interface.actor2 = \
                self.drawLineBetween2Landmark(self.interface.line2LAComboBox.currentText,
                                              self.interface.line2LBComboBox.currentText,
                                              self.interface.fidListComboBoxline2LA.currentNode(),
                                              self.interface.fidListComboBoxline2LB.currentNode())
        if self.interface.lineLAComboBox.currentText != '' and self.interface.lineLBComboBox.currentText != '' \
                and self.interface.lineLAComboBox.currentText != self.interface.lineLBComboBox.currentText :
            if self.interface.renderer3 :
                self.interface.renderer3.RemoveActor(self.interface.actor3)
            self.interface.renderer3, self.interface.actor3 = \
                self.drawLineBetween2Landmark(self.interface.lineLAComboBox.currentText,
                                              self.interface.lineLBComboBox.currentText,
                                              self.interface.fidListComboBoxlineLA.currentNode(),
                                              self.interface.fidListComboBoxlineLB.currentNode())

    def updateMidPoint(self, fidList, landmarkID):
        landmarkDescription = self.decodeJSON(fidList.GetAttribute("landmarkDescription"))
        for midPointID in landmarkDescription[landmarkID]["midPoint"]["definedByThisMarkup"]:
            if landmarkDescription[midPointID]["midPoint"]["isMidPoint"]:
                landmark1ID = landmarkDescription[midPointID]["midPoint"]["Point1"]
                landmark2ID = landmarkDescription[midPointID]["midPoint"]["Point2"]
                coord = self.calculateMidPointCoord(fidList, landmark1ID, landmark2ID)
                index = fidList.GetNthControlPointIndexByID(midPointID)
                fidList.SetNthFiducialPositionFromArray(index, coord)
                if landmarkDescription[midPointID]["projection"]["isProjected"]:
                    hardenModel = slicer.app.mrmlScene().GetNodeByID(fidList.GetAttribute("hardenModelID"))
                    landmarkDescription[midPointID]["projection"]["closestPointIndex"] = \
                        self.projectOnSurface(hardenModel, fidList, midPointID)
                    fidList.SetAttribute("landmarkDescription",self.encodeJSON(landmarkDescription))
                self.updateMidPoint(fidList, midPointID)

    # Called when a landmarks is moved
    def onPointModifiedEvent(self, obj, event):
        print("----onPointModifiedEvent Q3DC-----")
        landmarkDescription = self.decodeJSON(obj.GetAttribute("landmarkDescription"))
        if not landmarkDescription:
            return
        selectedLandmarkID = self.findIDFromLabel(obj, self.interface.landmarkComboBox.currentText)
        # remove observer to make sure, the callback function won't work..
        tag = self.decodeJSON(obj.GetAttribute("PointModifiedEventTag"))
        obj.RemoveObserver(tag["PointModifiedEventTag"])
        if selectedLandmarkID:
            activeLandmarkState = landmarkDescription[selectedLandmarkID]
            print(activeLandmarkState)
            if activeLandmarkState["projection"]["isProjected"]:
                hardenModel = slicer.app.mrmlScene().GetNodeByID(obj.GetAttribute("hardenModelID"))
                activeLandmarkState["projection"]["closestPointIndex"] = \
                    self.projectOnSurface(hardenModel, obj, selectedLandmarkID)
                obj.SetAttribute("landmarkDescription",self.encodeJSON(landmarkDescription))
            self.updateMidPoint(obj,selectedLandmarkID)
            self.findROI(obj)
        time.sleep(0.08)
        # Add the observer again
        PointModifiedEventTag = obj.AddObserver(obj.PointModifiedEvent, self.onPointModifiedEvent)
        obj.SetAttribute("PointModifiedEventTag",self.encodeJSON({"PointModifiedEventTag":PointModifiedEventTag}))

    def onPointRemovedEvent(self, obj, event):
        print("------markup deleting-------")
        landmarkDescription = self.decodeJSON(obj.GetAttribute("landmarkDescription"))
        IDs = []
        for ID, value in landmarkDescription.items():
            isFound = False
            for n in range(obj.GetNumberOfMarkups()):
                markupID = obj.GetNthMarkupID(n)
                if ID == markupID:
                    isFound = True
            if not isFound:
                IDs.append(ID)
        for ID in IDs:
            self.deleteLandmark(obj, landmarkDescription[ID]["landmarkLabel"])
            landmarkDescription.pop(ID,None)
        obj.SetAttribute("landmarkDescription",self.encodeJSON(landmarkDescription))

    def addLandmarkToCombox(self, fidList, combobox, markupID):
        if not fidList:
            return
        landmarkDescription = self.decodeJSON(fidList.GetAttribute("landmarkDescription"))
        combobox.addItem(landmarkDescription[markupID]["landmarkLabel"])

    def updateAllLandmarkComboBox(self, fidList, markupID):
        # update of the Combobox that are always updated
        self.updateLandmarkComboBox(fidList, self.interface.landmarkComboBox, False)
        self.addLandmarkToCombox(fidList, self.interface.landmarkComboBox1, markupID)
        self.addLandmarkToCombox(fidList, self.interface.landmarkComboBox2, markupID)
        #update of the Comboboxes that display the fidcial list just modified
        for key,value in self.comboboxdict.items():
            if value is fidList:
                self.addLandmarkToCombox(fidList, key, markupID)

    def updateLandmarkComboBox(self, fidList, combobox, displayMidPoint = True):
        combobox.blockSignals(True)
        combobox.clear()
        if not fidList:
            return
        landmarkDescription = self.decodeJSON(fidList.GetAttribute("landmarkDescription"))
        if not fidList:
            return
        numOfFid = fidList.GetNumberOfMarkups()
        if numOfFid > 0:
            for i in range(0, numOfFid):
                if displayMidPoint is False:
                    ID = fidList.GetNthMarkupID(i)
                    if not landmarkDescription[ID]["midPoint"]["isMidPoint"]:
                        landmarkLabel = fidList.GetNthMarkupLabel(i)
                        combobox.addItem(landmarkLabel)
                else:
                    landmarkLabel = fidList.GetNthMarkupLabel(i)
                    combobox.addItem(landmarkLabel)
        combobox.setCurrentIndex(combobox.count - 1)
        combobox.blockSignals(False)

    def deleteLandmark(self, fidList, label):
        # update of the Combobox that are always updated
        self.interface.landmarkComboBox.removeItem(self.interface.landmarkComboBox.findText(label))
        self.interface.landmarkComboBox1.removeItem(self.interface.landmarkComboBox1.findText(label))
        self.interface.landmarkComboBox2.removeItem(self.interface.landmarkComboBox2.findText(label))
        for key,value in self.comboboxdict.items():
            if value is fidList:
                key.removeItem(key.findText(label))

    @staticmethod
    def findIDFromLabel(fidList, landmarkLabel):
        # find the ID of the markupsNode from the label of a landmark!
        for i in range(fidList.GetNumberOfFiducials()):
            if landmarkLabel == fidList.GetNthFiducialLabel(i):
                return fidList.GetNthMarkupID(i)
        return None

    def getClosestPointIndex(self, fidNode, inputPolyData, landmarkID):
        landmarkCoord = np.zeros(3)
        landmarkCoord[1] = 42
        fidNode.GetNthFiducialPosition(landmarkID, landmarkCoord)
        pointLocator = vtk.vtkPointLocator()
        pointLocator.SetDataSet(inputPolyData)
        pointLocator.AutomaticOn()
        pointLocator.BuildLocator()
        indexClosestPoint = pointLocator.FindClosestPoint(landmarkCoord)
        return indexClosestPoint

    def replaceLandmark(self, inputModelPolyData, fidNode, landmarkID, indexClosestPoint):
        landmarkCoord = [-1, -1, -1]
        inputModelPolyData.GetPoints().GetPoint(indexClosestPoint, landmarkCoord)
        print(landmarkCoord)
        fidNode.SetNthFiducialPositionFromArray(landmarkID,landmarkCoord)

    def projectOnSurface(self, modelOnProject, fidNode, selectedFidReflID):
        if selectedFidReflID:
            markupsIndex = fidNode.GetNthControlPointIndexByID(selectedFidReflID)
            indexClosestPoint = self.getClosestPointIndex(fidNode, modelOnProject.GetPolyData(), markupsIndex)
            self.replaceLandmark(modelOnProject.GetPolyData(), fidNode, markupsIndex, indexClosestPoint)
            return indexClosestPoint

    def calculateMidPointCoord(self, fidList, landmark1ID, landmark2ID):
        """Set the midpoint when you know the the mrml nodes"""
        landmark1Index = fidList.GetNthControlPointIndexByID(landmark1ID)
        landmark2Index = fidList.GetNthControlPointIndexByID(landmark2ID)
        coord1 = [-1, -1, -1]
        coord2 = [-1, -1, -1]
        fidList.GetNthFiducialPosition(landmark1Index, coord1)
        fidList.GetNthFiducialPosition(landmark2Index, coord2)
        midCoord = [-1, -1, -1]
        midCoord[0] = (coord1[0] + coord2[0])/2
        midCoord[1] = (coord1[1] + coord2[1])/2
        midCoord[2] = (coord1[2] + coord2[2])/2
        return midCoord

    def removecomponentFromStorage(self, type, element):
        if type == 'angles':
            element.Yaw = None
            element.Roll = None
            element.Pitch = None
        if type == 'distance':
            element.RLComponent = None
            element.APComponent = None
            element.SIComponent = None
            element.ThreeDComponent = None
        return element

    def defineDistances(self, markupsNode1, landmark1Index, markupsNode2, landmark2Index):
        coord1 = [-1, -1, -1]
        coord2 = [-1, -1, -1]
        markupsNode1.GetNthFiducialPosition(landmark1Index, coord1)
        markupsNode2.GetNthFiducialPosition(landmark2Index, coord2)
        diffRAxis = coord2[0] - coord1[0]
        diffAAxis = coord2[1] - coord1[1]
        diffSAxis = coord2[2] - coord1[2]
        threeDDistance = math.sqrt(vtk.vtkMath().Distance2BetweenPoints(coord1, coord2))
        return round(diffRAxis, self.numberOfDecimals),\
               round(diffAAxis, self.numberOfDecimals),\
               round(diffSAxis, self.numberOfDecimals),\
               round(threeDDistance, self.numberOfDecimals)

    def addOnDistanceList(self, distanceList, fidLabel1, fidLabel2, fidlist1, fidlist2):
        fidID1 = self.findIDFromLabel(fidlist1,fidLabel1)
        fidID2 = self.findIDFromLabel(fidlist2,fidLabel2)
        landmark1Index = fidlist1.GetNthControlPointIndexByID(fidID1)
        landmark2Index = fidlist2.GetNthControlPointIndexByID(fidID2)
        elementToAdd = self.distanceValuesStorage()
        # if this distance has already been computed before -> replace values
        for element in distanceList:
            if element.startLandmarkID == fidID1 and element.endLandmarkID == fidID2:
                element = self.removecomponentFromStorage('distance', element)
                element.startLandmarkName = fidLabel1
                element.endLandmarkName = fidLabel2
                element.RLComponent, element.APComponent, element.SIComponent, element.ThreeDComponent = \
                    self.defineDistances(fidlist1, landmark1Index, fidlist2, landmark2Index)
                return distanceList
        elementToAdd.startLandmarkID = fidID1
        elementToAdd.endLandmarkID = fidID2
        elementToAdd.startLandmarkName = fidLabel1
        elementToAdd.endLandmarkName = fidLabel2
        elementToAdd.RLComponent, elementToAdd.APComponent, elementToAdd.SIComponent, elementToAdd.ThreeDComponent = \
            self.defineDistances(fidlist1, landmark1Index, fidlist2, landmark2Index)
        distanceList.append(elementToAdd)
        return distanceList

    def defineDistanceTable(self, table, distanceList):
        table.clear()
        table.setRowCount(distanceList.__len__())
        table.setColumnCount(5)
        table.setMinimumHeight(50*distanceList.__len__())
        table.setHorizontalHeaderLabels(['  ', ' R-L Component', ' A-P Component', ' S-I Component', ' 3D Distance '])
        i = 0
        for element in distanceList:
            startLandName = element.startLandmarkName
            endLandName = element.endLandmarkName
            label = qt.QLabel(' ' + startLandName + ' - ' + endLandName + ' ')
            label.setStyleSheet('QLabel{qproperty-alignment:AlignCenter;}')
            table.setCellWidget(i, 0,label)
            if element.RLComponent != None:
                label = qt.QLabel(element.RLComponent)
                label.setStyleSheet('QLabel{qproperty-alignment:AlignCenter;}')
                table.setCellWidget(i, 1, label)
            else:
                label = qt.QLabel(' - ')
                label.setStyleSheet('QLabel{qproperty-alignment:AlignCenter;}')
                table.setCellWidget(i, 1, label)

            if element.APComponent != None:
                label = qt.QLabel(element.APComponent)
                label.setStyleSheet('QLabel{qproperty-alignment:AlignCenter;}')
                table.setCellWidget(i, 2, label)
            else:
                label = qt.QLabel(' - ')
                label.setStyleSheet('QLabel{qproperty-alignment:AlignCenter;}')
                table.setCellWidget(i, 2, label)

            if element.SIComponent != None:
                label = qt.QLabel(element.SIComponent)
                label.setStyleSheet('QLabel{qproperty-alignment:AlignCenter;}')
                table.setCellWidget(i, 3, label)
            else:
                label = qt.QLabel(' - ')
                label.setStyleSheet('QLabel{qproperty-alignment:AlignCenter;}')
                table.setCellWidget(i, 3, label)

            if element.ThreeDComponent != None:
                label = qt.QLabel(element.ThreeDComponent)
                label.setStyleSheet('QLabel{qproperty-alignment:AlignCenter;}')
                table.setCellWidget(i, 4, label)
            else:
                label = qt.QLabel(' - ')
                label.setStyleSheet('QLabel{qproperty-alignment:AlignCenter;}')
                table.setCellWidget(i, 4, label)

            i += 1
        return table

    def computePitch(self, markupsNode1, landmark1Index,
                     markupsNode2, landmark2Index,
                     markupsNode3, landmark3Index,
                     markupsNode4, landmark4Index):
        # Pitch is computed by projection on the plan (y,z)
        coord1 = [-1, -1, -1]
        coord2 = [-1, -1, -1]
        coord3 = [-1, -1, -1]
        coord4 = [-1, -1, -1]

        markupsNode1.GetNthFiducialPosition(landmark1Index, coord1)
        markupsNode2.GetNthFiducialPosition(landmark2Index, coord2)
        markupsNode3.GetNthFiducialPosition(landmark3Index, coord3)
        markupsNode4.GetNthFiducialPosition(landmark4Index, coord4)

        vectLine1 = [0, coord2[1]-coord1[1], coord2[2]-coord1[2] ]
        normVectLine1 = np.sqrt( vectLine1[1]*vectLine1[1] + vectLine1[2]*vectLine1[2] )
        vectLine2 = [0, coord4[1]-coord3[1], coord4[2]-coord3[2] ]
        normVectLine2 = np.sqrt( vectLine2[1]*vectLine2[1] + vectLine2[2]*vectLine2[2] )
        pitchNotSigned = round(vtk.vtkMath().DegreesFromRadians(vtk.vtkMath().AngleBetweenVectors(vectLine1, vectLine2)),
                               self.numberOfDecimals)

        if normVectLine1 != 0 and normVectLine2 != 0:
            normalizedVectLine1 = [0, (1/normVectLine1)*vectLine1[1], (1/normVectLine1)*vectLine1[2]]
            normalizedVectLine2 = [0, (1/normVectLine2)*vectLine2[1], (1/normVectLine2)*vectLine2[2]]
            det2D = normalizedVectLine1[1]*normalizedVectLine2[2] - normalizedVectLine1[2]*normalizedVectLine2[1]
            return math.copysign(pitchNotSigned, det2D)
        else:
            slicer.util.errorDisplay("ERROR, norm of your vector is 0! DEFINE A VECTOR!")
            return None

    def computeRoll(self, markupsNode1, landmark1Index,
                    markupsNode2, landmark2Index,
                    markupsNode3, landmark3Index,
                    markupsNode4, landmark4Index):
        # Roll is computed by projection on the plan (x,z)
        coord1 = [-1, -1, -1]
        coord2 = [-1, -1, -1]
        coord3 = [-1, -1, -1]
        coord4 = [-1, -1, -1]

        markupsNode1.GetNthFiducialPosition(landmark1Index, coord1)
        markupsNode2.GetNthFiducialPosition(landmark2Index, coord2)
        markupsNode3.GetNthFiducialPosition(landmark3Index, coord3)
        markupsNode4.GetNthFiducialPosition(landmark4Index, coord4)

        vectLine1 = [coord2[0]-coord1[0], 0, coord2[2]-coord1[2] ]
        normVectLine1 = np.sqrt( vectLine1[0]*vectLine1[0] + vectLine1[2]*vectLine1[2] )
        vectLine2 = [coord4[0]-coord3[0], 0, coord4[2]-coord3[2] ]
        normVectLine2 = np.sqrt( vectLine2[0]*vectLine2[0] + vectLine2[2]*vectLine2[2] )
        rollNotSigned = round(vtk.vtkMath().DegreesFromRadians(vtk.vtkMath().AngleBetweenVectors(vectLine1, vectLine2)),
                              self.numberOfDecimals)

        if normVectLine1 != 0 and normVectLine2 != 0:
            normalizedVectLine1 = [(1/normVectLine1)*vectLine1[0], 0, (1/normVectLine1)*vectLine1[2]]
            normalizedVectLine2 = [(1/normVectLine2)*vectLine2[0], 0, (1/normVectLine2)*vectLine2[2]]
            det2D = normalizedVectLine1[0]*normalizedVectLine2[2] - normalizedVectLine1[2]*normalizedVectLine2[0]
            return math.copysign(rollNotSigned, det2D)
        else:
            print (" ERROR, norm of your vector is 0! DEFINE A VECTOR!")
            return None

    def computeYaw(self, markupsNode1, landmark1Index,
                   markupsNode2, landmark2Index,
                   markupsNode3, landmark3Index,
                   markupsNode4, landmark4Index):
        # Yaw is computed by projection on the plan (x,y)
        coord1 = [-1, -1, -1]
        coord2 = [-1, -1, -1]
        coord3 = [-1, -1, -1]
        coord4 = [-1, -1, -1]

        markupsNode1.GetNthFiducialPosition(landmark1Index, coord1)
        markupsNode2.GetNthFiducialPosition(landmark2Index, coord2)
        markupsNode3.GetNthFiducialPosition(landmark3Index, coord3)
        markupsNode4.GetNthFiducialPosition(landmark4Index, coord4)

        vectLine1 = [coord2[0]-coord1[0], coord2[1]-coord1[1], 0 ]
        normVectLine1 = np.sqrt( vectLine1[0]*vectLine1[0] + vectLine1[1]*vectLine1[1] )
        vectLine2 = [coord4[0]-coord3[0],coord4[1]-coord3[1], 0]
        normVectLine2 = np.sqrt( vectLine2[0]*vectLine2[0] + vectLine2[1]*vectLine2[1] )
        yawNotSigned = round(vtk.vtkMath().DegreesFromRadians(vtk.vtkMath().AngleBetweenVectors(vectLine1, vectLine2)),
                             self.numberOfDecimals)

        if normVectLine1 != 0 and normVectLine2 != 0:
            normalizedVectLine1 = [(1/normVectLine1)*vectLine1[0], (1/normVectLine1)*vectLine1[1], 0]
            normalizedVectLine2 = [(1/normVectLine2)*vectLine2[0], (1/normVectLine2)*vectLine2[1], 0]
            det2D = normalizedVectLine1[0]*normalizedVectLine2[1] - normalizedVectLine1[1]*normalizedVectLine2[0]
            return math.copysign(yawNotSigned, det2D)
        else:
            slicer.util.errorDisplay("ERROR, norm of your vector is 0! DEFINE A VECTOR!")
            return None

    def addOnAngleList(self, angleList,
                       fidLabel1A, fidLabel1B, fidlist1A, fidlist1B,
                       fidLabel2A, fidLabel2B, fidlist2A, fidlist2B,
                       PitchState, YawState, RollState):
        fidID1A = self.findIDFromLabel(fidlist1A,fidLabel1A)
        fidID1B = self.findIDFromLabel(fidlist1B,fidLabel1B)
        fidID2A = self.findIDFromLabel(fidlist2A,fidLabel2A)
        fidID2B = self.findIDFromLabel(fidlist2B,fidLabel2B)
        landmark1Index = fidlist1A.GetNthControlPointIndexByID(fidID1A)
        landmark2Index = fidlist1B.GetNthControlPointIndexByID(fidID1B)
        landmark3Index = fidlist2A.GetNthControlPointIndexByID(fidID2A)
        landmark4Index = fidlist2B.GetNthControlPointIndexByID(fidID2B)
        # if angles has already been computed before -> replace values
        elementToAdd = self.angleValuesStorage()
        for element in angleList:
            if element.landmarkALine1ID == fidID1A and\
                            element.landmarkBLine1ID == fidID1B and\
                            element.landmarkALine2ID == fidID2A and\
                            element.landmarkBLine2ID == fidID2B:
                element = self.removecomponentFromStorage('angles', element)
                if PitchState:
                    element.Pitch = self.computePitch(fidlist1A, landmark1Index,
                                                      fidlist1B, landmark2Index,
                                                      fidlist2A, landmark3Index,
                                                      fidlist2B, landmark4Index)
                if RollState:
                    element.Roll = self.computeRoll(fidlist1A, landmark1Index,
                                                    fidlist1B, landmark2Index,
                                                    fidlist2A, landmark3Index,
                                                    fidlist2B, landmark4Index)
                if YawState:
                    element.Yaw = self.computeYaw(fidlist1A, landmark1Index,
                                                  fidlist1B, landmark2Index,
                                                  fidlist2A, landmark3Index,
                                                  fidlist2B, landmark4Index)
                element.landmarkALine1Name = fidLabel1A
                element.landmarkBLine1Name = fidLabel1B
                element.landmarkALine2Name = fidLabel2A
                element.landmarkBLine2Name = fidLabel2B
                return angleList
        # create a new element depending on what the user wants
        elementToAdd.landmarkALine1ID = fidID1A
        elementToAdd.landmarkBLine1ID = fidID1B
        elementToAdd.landmarkALine2ID = fidID2A
        elementToAdd.landmarkBLine2ID = fidID2B
        elementToAdd.landmarkALine1Name = fidLabel1A
        elementToAdd.landmarkBLine1Name = fidLabel1B
        elementToAdd.landmarkALine2Name = fidLabel2A
        elementToAdd.landmarkBLine2Name = fidLabel2B
        if PitchState:
            elementToAdd.Pitch = self.computePitch(fidlist1A, landmark1Index,
                                                   fidlist1B, landmark2Index,
                                                   fidlist2A, landmark3Index,
                                                   fidlist2B, landmark4Index)
        if RollState:
            elementToAdd.Roll = self.computeRoll(fidlist1A, landmark1Index,
                                                 fidlist1B, landmark2Index,
                                                 fidlist2A, landmark3Index,
                                                 fidlist2B, landmark4Index)
        if YawState:
            elementToAdd.Yaw = self.computeYaw(fidlist1A, landmark1Index,
                                               fidlist1B, landmark2Index,
                                               fidlist2A, landmark3Index,
                                               fidlist2B, landmark4Index)
        angleList.append(elementToAdd)
        return angleList

    def defineAnglesTable(self, table, angleList):

        table.clear()
        table.setRowCount(angleList.__len__())
        table.setColumnCount(4)
        table.setMinimumHeight(50*angleList.__len__())
        table.setHorizontalHeaderLabels([' ', ' YAW ', ' PITCH ', ' ROLL '])
        i = 0

        for element in angleList:
            landmarkALine1Name = element.landmarkALine1Name
            landmarkBLine1Name = element.landmarkBLine1Name
            landmarkALine2Name = element.landmarkALine2Name
            landmarkBLine2Name = element.landmarkBLine2Name

            label = qt.QLabel(' ' + landmarkALine1Name + '-' + landmarkBLine1Name + ' / ' + landmarkALine2Name + '-' + landmarkBLine2Name)
            label.setStyleSheet('QLabel{qproperty-alignment:AlignCenter;}')
            table.setCellWidget(i, 0, label)
            if element.Yaw != None:
                sign = np.sign(element.Yaw)
                label = qt.QLabel(str(element.Yaw)+' / '+str(sign*(180-abs(element.Yaw))))
                label.setStyleSheet('QLabel{qproperty-alignment:AlignCenter;}')
                table.setCellWidget(i, 1, label)
            else:
                label = qt.QLabel(' - ')
                label.setStyleSheet('QLabel{qproperty-alignment:AlignCenter;}')
                table.setCellWidget(i, 1, label)

            if element.Pitch != None:
                sign = np.sign(element.Pitch)
                label = qt.QLabel(str(element.Pitch) + ' / ' + str(sign*(180 - abs(element.Pitch))))
                label.setStyleSheet('QLabel{qproperty-alignment:AlignCenter;}')
                table.setCellWidget(i, 2, label)
            else:
                label = qt.QLabel(' - ')
                label.setStyleSheet('QLabel{qproperty-alignment:AlignCenter;}')
                table.setCellWidget(i, 2, label)

            if element.Roll != None:
                sign = np.sign(element.Roll)
                label = qt.QLabel(str(element.Roll) + ' / ' + str(sign * (180 - abs(element.Roll))))
                label.setStyleSheet('QLabel{qproperty-alignment:AlignCenter;}')
                table.setCellWidget(i, 3, label)
            else:
                label = qt.QLabel(' - ')
                label.setStyleSheet('QLabel{qproperty-alignment:AlignCenter;}')
                table.setCellWidget(i, 3, label)

            i += 1
        return table

    def defineDistancesLinePoint(self, markupsNodeLine1, landmarkLine1Index,
                                 markupsNodeLine2, landmarkLine2Index,
                                 markupsNodepoint, landmarkpointIndex):
        line = vtk.vtkLine()
        coordLine1 = [-1, -1, -1]
        coordLine2 = [-1, -1, -1]
        coordPoint = [-1, -1, -1]
        markupsNodeLine1.GetNthFiducialPosition(landmarkLine1Index, coordLine1)
        markupsNodeLine2.GetNthFiducialPosition(landmarkLine2Index, coordLine2)
        markupsNodepoint.GetNthFiducialPosition(landmarkpointIndex, coordPoint)
        parametric = vtk.mutable(0)
        projectCoord = [0, 0, 0]
        distance = line.DistanceToLine(coordPoint, coordLine1, coordLine2, parametric, projectCoord)
        diffRAxis = coordPoint[0] - projectCoord[0]
        diffAAxis = coordPoint[1] - projectCoord[1]
        diffSAxis = coordPoint[2] - projectCoord[2]
        return round(diffRAxis, self.numberOfDecimals), \
               round(diffAAxis, self.numberOfDecimals), \
               round(diffSAxis, self.numberOfDecimals), \
               round(math.sqrt(distance), self.numberOfDecimals)

    def addOnLinePointList(self, linePointList,
                           fidLabelLineA, fidLabelLineB,
                           fidListLineLA, fidListLineLB,
                           fidLabelPoint, fidListPoint):
        lineLAID = self.findIDFromLabel(fidListLineLA, fidLabelLineA)
        lineLAIndex = fidListLineLA.GetNthControlPointIndexByID(lineLAID)
        lineLBID = self.findIDFromLabel(fidListLineLB, fidLabelLineB)
        lineLBIndex = fidListLineLB.GetNthControlPointIndexByID(lineLBID)
        PointID = self.findIDFromLabel(fidListPoint, fidLabelPoint)
        PointIndex = fidListPoint.GetNthControlPointIndexByID(PointID)
        elementToAdd = self.distanceLinePointStorage()
        # if this distance has already been computed before -> replace values
        for element in linePointList:
            if element.landmarkALineID == lineLAID and \
                            element.landmarkBLineID == lineLBID and\
                            element.landmarkPointID == PointID:
                element = self.removecomponentFromStorage('distance', element)
                element.landmarkALineID = lineLAID
                element.landmarkBLineID = lineLBID
                element.landmarkPointID = PointID
                element.landmarkALineName = fidLabelLineA
                element.landmarkBLineName = fidLabelLineB
                element.landmarkPointName = fidLabelPoint
                element.RLComponent, element.APComponent, element.SIComponent, element.ThreeDComponent = \
                    self.defineDistancesLinePoint(fidListLineLA, lineLAIndex,
                                                  fidListLineLB, lineLBIndex,
                                                  fidListPoint, PointIndex)
                return linePointList
        elementToAdd.landmarkALineID = lineLAID
        elementToAdd.landmarkBLineID = lineLBID
        elementToAdd.landmarkPointID = PointID
        elementToAdd.landmarkALineName = fidLabelLineA
        elementToAdd.landmarkBLineName = fidLabelLineB
        elementToAdd.landmarkPointName = fidLabelPoint
        elementToAdd.RLComponent, elementToAdd.APComponent, elementToAdd.SIComponent, elementToAdd.ThreeDComponent = \
            self.defineDistancesLinePoint(fidListLineLA, lineLAIndex,
                                          fidListLineLB, lineLBIndex,
                                          fidListPoint, PointIndex)
        linePointList.append(elementToAdd)
        return linePointList

    def defineDistanceLinePointTable(self, table, distanceList):
        table.clear()
        table.setRowCount(distanceList.__len__())
        table.setColumnCount(5)
        table.setMinimumHeight(50*distanceList.__len__())
        table.setHorizontalHeaderLabels(['  ', ' R-L Component', ' A-P Component', ' S-I Component', ' 3D Distance '])
        i = 0
        for element in distanceList:
            landmarkALineName = element.landmarkALineName
            landmarkBLineName = element.landmarkBLineName
            landmarkPoint = element.landmarkPointName

            label = qt.QLabel(' ' + str(landmarkALineName) + ' - ' + str(landmarkBLineName) + ' / ' + str(landmarkPoint) + ' ')
            label.setStyleSheet('QLabel{qproperty-alignment:AlignCenter;}')
            table.setCellWidget(i, 0,label)
            if element.RLComponent != None:
                label = qt.QLabel(element.RLComponent)
                label.setStyleSheet('QLabel{qproperty-alignment:AlignCenter;}')
                table.setCellWidget(i, 1, label)
            else:
                label = qt.QLabel(' - ')
                label.setStyleSheet('QLabel{qproperty-alignment:AlignCenter;}')
                table.setCellWidget(i, 1, label)

            if element.APComponent != None:
                label = qt.QLabel(element.APComponent)
                label.setStyleSheet('QLabel{qproperty-alignment:AlignCenter;}')
                table.setCellWidget(i, 2, label)
            else:
                label = qt.QLabel(' - ')
                label.setStyleSheet('QLabel{qproperty-alignment:AlignCenter;}')
                table.setCellWidget(i, 2, label)

            if element.SIComponent != None:
                label = qt.QLabel(element.SIComponent)
                label.setStyleSheet('QLabel{qproperty-alignment:AlignCenter;}')
                table.setCellWidget(i, 3, label)
            else:
                label = qt.QLabel(' - ')
                label.setStyleSheet('QLabel{qproperty-alignment:AlignCenter;}')
                table.setCellWidget(i, 3, label)

            if element.ThreeDComponent != None:
                label = qt.QLabel(element.ThreeDComponent)
                label.setStyleSheet('QLabel{qproperty-alignment:AlignCenter;}')
                table.setCellWidget(i, 4, label)
            else:
                label = qt.QLabel(' - ')
                label.setStyleSheet('QLabel{qproperty-alignment:AlignCenter;}')
                table.setCellWidget(i, 4, label)

            i += 1
        return table

    def drawLineBetween2Landmark(self, landmark1label, landmark2label, fidList1, fidList2):
        if not fidList1 or not fidList2 or not landmark1label or not landmark2label:
            return
        landmark1ID = self.findIDFromLabel(fidList1, landmark1label)
        landmark2ID = self.findIDFromLabel(fidList2, landmark2label)

        if not fidList1 or not fidList2:
            return None, None
        landmark1Index = fidList1.GetNthControlPointIndexByID(landmark1ID)
        landmark2Index = fidList2.GetNthControlPointIndexByID(landmark2ID)

        coord1 = [-1, -1, -1]
        coord2 = [-1, -1, -1]
        fidList1.GetNthFiducialPosition(landmark1Index, coord1)
        fidList2.GetNthFiducialPosition(landmark2Index, coord2)

        line = vtk.vtkLineSource()
        line.SetPoint1(coord1)
        line.SetPoint2(coord2)
        line.Update()

        mapper = vtk.vtkPolyDataMapper()
        actor = vtk.vtkActor()
        mapper.SetInputData(line.GetOutput())
        mapper.Update()
        actor.SetMapper(mapper)

        layoutManager = slicer.app.layoutManager()
        threeDWidget = layoutManager.threeDWidget(0)
        threeDView = threeDWidget.threeDView()
        renderWindow = threeDView.renderWindow()
        renderers = renderWindow.GetRenderers()
        renderer = renderers.GetFirstRenderer()
        renderWindow.AddRenderer(renderer)
        renderer.AddActor(actor)
        renderWindow.Render()

        return renderer, actor

    def exportationFunction(self, directoryExport, filenameExport, listToExport, typeCalculation):
        messageBox = ctk.ctkMessageBox()
        messageBox.setWindowTitle(' /!\ WARNING /!\ ')
        messageBox.setIcon(messageBox.Warning)

        fileName = os.path.join(directoryExport.directory, filenameExport.text)
        if os.path.exists(fileName):
            messageBox.setText('File ' + fileName + ' already exists!')
            messageBox.setInformativeText('Do you want to replace it ?')
            messageBox.setStandardButtons( messageBox.No | messageBox.Yes)
            choice = messageBox.exec_()
            if choice == messageBox.No:
                return
        self.exportAsCSV(fileName, listToExport, typeCalculation)
        slicer.util.delayDisplay(f'Saved to {fileName}')


    def exportAsCSV(self,filename, listToExport, typeCalculation):
        #  Export fields on different csv files
        file = open(filename, 'w')
        cw = csv.writer(file, delimiter=',')
        print(typeCalculation)
        if typeCalculation == 'distance':
            cw.writerow([' Landmark A - Landmark B',  ' R-L Component', ' A-P Component', ' S-I Component', ' 3D Distance '])
            self.writeDistance(cw, listToExport)
        elif typeCalculation == 'linePoint':
            cw.writerow([' Landmark A - Landmark B / Landmark X',  ' R-L Component', ' A-P Component', ' S-I Component', ' 3D Distance '])
            self.writeLinePoint(cw, listToExport)
        else:
            cw.writerow([' Line 1 (Landmark A - Landmark B) |  Line 2 (Landmark A - Landmark B)',  ' YAW ', ' PITCH ', ' ROLL '])
            self.writeAngle(cw, listToExport)
        file.close()
        if self.decimalPoint != '.':
            self.replaceCharac(filename, ',', ';') # change the Delimiter and put a semicolon instead of a comma
            self.replaceCharac(filename, '.', self.decimalPoint) # change the decimal separator '.' for a comma

    def writeDistance(self, fileWriter, listToExport):
        for element in listToExport:
            startLandName = element.startLandmarkName
            endLandName = element.endLandmarkName
            label = startLandName + ' - ' + endLandName
            fileWriter.writerow([label,
                                 element.RLComponent,
                                 element.APComponent,
                                 element.SIComponent,
                                 element.ThreeDComponent])

    def writeLinePoint(self, fileWriter, listToExport):
        for element in listToExport:
            landmarkALineName = element.landmarkALineName
            landmarkBLineName = element.landmarkBLineName
            landmarkPoint = element.landmarkPointName
            label = landmarkALineName + ' - ' + landmarkBLineName + ' / ' + landmarkPoint
            fileWriter.writerow([label,
                                 element.RLComponent,
                                 element.APComponent,
                                 element.SIComponent,
                                 element.ThreeDComponent])

    def writeAngle(self, fileWriter, listToExport):
        for element in listToExport:
            print("element")
            print(element)
            landmarkALine1Name = element.landmarkALine1Name
            landmarkBLine1Name = element.landmarkBLine1Name
            landmarkALine2Name = element.landmarkALine2Name
            landmarkBLine2Name = element.landmarkBLine2Name

            label = landmarkALine1Name + '-' + landmarkBLine1Name + ' | ' + landmarkALine2Name + '-' + landmarkBLine2Name
            signY = np.sign(element.Yaw)
            signP = np.sign(element.Pitch)
            signR = np.sign(element.Roll)

            if element.Yaw:
                YawLabel = str(element.Yaw) +' | '+str(signY*(180-abs(element.Yaw)))
            else:
                YawLabel = '-'

            if element.Pitch:
                PitchLabel = str(element.Pitch)+' | '+str(signP*(180-abs(element.Pitch)))
            else:
                PitchLabel = '-'

            if element.Roll:
                RollLabel = str(element.Roll)+' | '+str(signR*(180-abs(element.Roll)))
            else:
                RollLabel = '-'

            fileWriter.writerow([label,
                                 YawLabel,
                                 PitchLabel,
                                 RollLabel])

    def replaceCharac(self, filename, oldCharac, newCharac):
        #  Function to replace a charactere (oldCharac) in a file (filename) by a new one (newCharac)
        file = open(filename,'r')
        lines = file.readlines()
        with open(filename, 'r') as file:
            lines = [line.replace(oldCharac, newCharac) for line in file.readlines()]
        file.close()
        file = open(filename, 'w')
        file.writelines(lines)
        file.close()

    def GetConnectedVertices(self, connectedVerticesIDList, polyData, pointID):
        # Return IDs of all the vertices that compose the first neighbor.
        cellList = vtk.vtkIdList()
        connectedVerticesIDList.InsertUniqueId(pointID)
        # Get cells that vertex 'pointID' belongs to
        polyData.GetPointCells(pointID, cellList)
        numberOfIds = cellList.GetNumberOfIds()
        for i in range(0, numberOfIds):
            # Get points which compose all cells
            pointIdList = vtk.vtkIdList()
            polyData.GetCellPoints(cellList.GetId(i), pointIdList)
            for j in range(0, pointIdList.GetNumberOfIds()):
                connectedVerticesIDList.InsertUniqueId(pointIdList.GetId(j))
        return connectedVerticesIDList

    def addArrayFromIdList(self, connectedIdList, inputModelNode, arrayName):
        if not inputModelNode:
            return
        inputModelNodePolydata = inputModelNode.GetPolyData()
        pointData = inputModelNodePolydata.GetPointData()
        numberofIds = connectedIdList.GetNumberOfIds()
        hasArrayInt = pointData.HasArray(arrayName)
        if hasArrayInt == 1:  # ROI Array found
            pointData.RemoveArray(arrayName)
        arrayToAdd = vtk.vtkDoubleArray()
        arrayToAdd.SetName(arrayName)
        for i in range(0, inputModelNodePolydata.GetNumberOfPoints()):
            arrayToAdd.InsertNextValue(0.0)
        for i in range(0, numberofIds):
            arrayToAdd.SetValue(connectedIdList.GetId(i), 1.0)
        lut = vtk.vtkLookupTable()
        tableSize = 2
        lut.SetNumberOfTableValues(tableSize)
        lut.Build()
        displayNode = inputModelNode.GetDisplayNode()
        rgb = displayNode.GetColor()
        lut.SetTableValue(0, rgb[0], rgb[1], rgb[2], 1)
        lut.SetTableValue(1, 1.0, 0.0, 0.0, 1)
        arrayToAdd.SetLookupTable(lut)
        pointData.AddArray(arrayToAdd)
        inputModelNodePolydata.Modified()
        return True

    def displayROI(self, inputModelNode, scalarName):
        PolyData = inputModelNode.GetPolyData()
        PolyData.Modified()
        displayNode = inputModelNode.GetModelDisplayNode()
        displayNode.SetScalarVisibility(False)
        with NodeModify(displayNode):
            displayNode.SetActiveScalarName(scalarName)
            displayNode.SetScalarVisibility(True)

    def findROI(self, fidList):
        hardenModel = slicer.app.mrmlScene().GetNodeByID(fidList.GetAttribute("hardenModelID"))
        connectedModel = slicer.app.mrmlScene().GetNodeByID(fidList.GetAttribute("connectedModelID"))
        landmarkDescription = self.decodeJSON(fidList.GetAttribute("landmarkDescription"))
        arrayName = fidList.GetAttribute("arrayName")
        ROIPointListID = vtk.vtkIdList()
        for key,activeLandmarkState in landmarkDescription.items():
            tempROIPointListID = vtk.vtkIdList()
            if activeLandmarkState["ROIradius"] != 0:
                self.defineNeighbor(tempROIPointListID,
                                    hardenModel.GetPolyData(),
                                    activeLandmarkState["projection"]["closestPointIndex"],
                                    activeLandmarkState["ROIradius"])
            for j in range(0, tempROIPointListID.GetNumberOfIds()):
                ROIPointListID.InsertUniqueId(tempROIPointListID.GetId(j))
        listID = ROIPointListID
        self.addArrayFromIdList(listID, connectedModel, arrayName)
        self.displayROI(connectedModel, arrayName)
        return ROIPointListID

    def warningMessage(self, message):
        messageBox = ctk.ctkMessageBox()
        messageBox.setWindowTitle(" /!\ WARNING /!\ ")
        messageBox.setIcon(messageBox.Warning)
        messageBox.setText(message)
        messageBox.setStandardButtons(messageBox.Ok)
        messageBox.exec_()

    def encodeJSON(self, input):
        encodedString = json.dumps(input)
        encodedString = encodedString.replace('\"', '\'')
        return encodedString

    def decodeJSON(self, input):
        if input:
            input = input.replace('\'','\"')
            return json.loads(input)
        return None

    def UpdateLandmarkComboboxA(self, fidListCombobox, landmarkCombobox):
        self.comboboxdict[landmarkCombobox] = fidListCombobox.currentNode()
        self.updateLandmarkComboBox(fidListCombobox.currentNode(), landmarkCombobox)
        self.UpdateInterface()


class Q3DCTest(ScriptedLoadableModuleTest):

    def setUp(self):
        """ Do whatever is needed to reset the state - typically a scene clear will be enough.
            """
        slicer.mrmlScene.Clear(0)

    def runTest(self):
        """Run as few or as many tests as needed here.
            """
        self.setUp()
        self.delayDisplay(' Starting tests ', 200)
        self.delayDisplay(' Test 3Dcomponents ')
        self.assertTrue(self.test_CalculateDisplacement1())
        self.delayDisplay(' Test Angles Components')
        self.assertTrue(self.test_CalculateDisplacement2())

        self.test_CalculateDisplacement1()
        self.test_CalculateDisplacement2()

        self.test_SimulateTutorial()
        self.delayDisplay(' Tests Passed! ')

    def test_CalculateDisplacement1(self):
        logic = Q3DCLogic(slicer.modules.Q3DCWidget)
        markupsNode1 = slicer.vtkMRMLMarkupsFiducialNode()
        markupsNode1.AddFiducial(-5.331, 51.955, 4.831)
        markupsNode1.AddFiducial(-8.018, 41.429, -52.621)
        diffXAxis, diffYAxis, diffZAxis, threeDDistance = logic.defineDistances(markupsNode1, 0, markupsNode1, 1)
        if diffXAxis != -2.687 or diffYAxis != -10.526 or diffZAxis != -57.452 or threeDDistance != 58.47:
            return False
        return True

    def test_CalculateDisplacement2(self):
        logic = Q3DCLogic(slicer.modules.Q3DCWidget)
        markupsNode1 = slicer.vtkMRMLMarkupsFiducialNode()

        markupsNode1.AddFiducial(63.90,-46.98, 6.98)
        markupsNode1.AddFiducial(43.79,-60.16,12.16)
        markupsNode1.AddFiducial(62.21,-45.31,7.41)
        markupsNode1.AddFiducial(41.97,-61.24,11.30)

        yaw = logic.computeYaw(markupsNode1, 0, markupsNode1, 1, markupsNode1, 2, markupsNode1, 3)
        roll = logic.computeRoll(markupsNode1, 0, markupsNode1, 1, markupsNode1, 2, markupsNode1, 3)
        if yaw != 4.964 or roll != 3.565:
            return False

        markupsNode1.AddFiducial(53.80,-53.57,9.47)
        markupsNode1.AddFiducial(53.98,-52.13,9.13)
        markupsNode1.AddFiducial(52.09,-53.27,9.36)
        markupsNode1.AddFiducial(51.77,-50.10,9.80)
        pitch = logic.computePitch(markupsNode1, 4, markupsNode1, 5, markupsNode1, 6, markupsNode1, 7)
        if pitch != 21.187:
            return False

        return True

    def test_SimulateTutorial(self):

        #
        # first, get the data - a zip file of example data
        #
        import urllib.request
        downloads = (
            ('http://slicer.kitware.com/midas3/download/item/211921/Q3DCExtensionTestData.zip', 'Q3DCExtensionTestData.zip'),
            )

        self.delayDisplay("Downloading")
        for url,name in downloads:
          filePath = slicer.app.temporaryPath + '/' + name
          if not os.path.exists(filePath) or os.stat(filePath).st_size == 0:
            self.delayDisplay('Requesting download %s from %s...\n' % (name, url))
            urllib.request.urlretrieve(url, filePath)
        self.delayDisplay('Finished with download\n')

        self.delayDisplay("Unzipping")
        q3dcFilesDirectory = slicer.app.temporaryPath + '/q3dcFiles'
        qt.QDir().mkpath(q3dcFilesDirectory)
        slicer.app.applicationLogic().Unzip(filePath, q3dcFilesDirectory)

        modelNodes = {}
        mandibleFiles = ("AH1m.vtk", "AH2m.vtk")
        for mandibleFile in mandibleFiles:
            name = os.path.splitext(mandibleFile)[0]
            self.delayDisplay("loading: %s" % name)
            filePath = q3dcFilesDirectory + "/" + mandibleFile
            success, modelNodes[name] = slicer.util.loadModel(filePath, returnNode=True)
            if not success:
                self.delayDisplay("load failed for %s" % filePath)
                return False

        modelNodes['AH2m'].GetDisplayNode().SetVisibility(0)
        modelNodes['AH1m'].GetDisplayNode().SetColor((1,0,0))

        self.delayDisplay("Enter markup mode")
        q3dcWidget = slicer.modules.Q3DCWidget

        points = ( (43, 25, -10), (-49, 22, -8), (-6, 64, -53) )

        firstMarkupsNode = None

        movingMarkupsFiducial = slicer.vtkMRMLMarkupsFiducialNode()
        movingMarkupsFiducial.SetName("F")
        slicer.mrmlScene.AddNode(movingMarkupsFiducial)
        q3dcWidget.inputModelSelector.setCurrentNode(modelNodes['AH2m'])
        q3dcWidget.inputLandmarksSelector.setCurrentNode(movingMarkupsFiducial)

        index = 0
        for point in points:
            q3dcWidget.onAddLandmarkButtonClicked()
            markupsNodeID = slicer.modules.markups.logic().GetActiveListID()
            if not markupsNodeID:
                self.delayDisplay("No markupsNodeID")
                return False
            markupsNode = slicer.util.getNode(markupsNodeID)
            if not markupsNode:
                self.delayDisplay("No markupsNode")
                return False
            markupsNode.AddFiducial(*point)
            if not firstMarkupsNode:
                firstMarkupsNode = markupsNode
            self.delayDisplay("Added point %d" % index)
            index += 1

        # reset the interaction node - since we are bypassing the clicks we don't need it
        interactionNode = slicer.mrmlScene.GetNodeByID("vtkMRMLInteractionNodeSingleton")
        interactionNode.SetCurrentInteractionMode(slicer.vtkMRMLInteractionNode.ViewTransform)

        self.delayDisplay("Define a middle point")
        q3dcWidget.midPointGroupBox.collapsed = False
        q3dcWidget.landmarkComboBox2.currentIndex = 1
        q3dcWidget.defineMiddlePointButton.clicked()
        midpointMarkupID = q3dcWidget.logic.findIDFromLabel(movingMarkupsFiducial,"F-4")
        if not midpointMarkupID:
            print ("Did not define a midpoint node")
            return False

        self.delayDisplay("Calculate a distance")
        q3dcWidget.distanceGroupBox.collapsed = False
        q3dcWidget.fidListComboBoxA.setCurrentNode(movingMarkupsFiducial)
        q3dcWidget.fidListComboBoxB.setCurrentNode(movingMarkupsFiducial)
        q3dcWidget.landmarkComboBoxA.currentIndex = 0
        q3dcWidget.landmarkComboBoxB.currentIndex = 1
        q3dcWidget.computeDistancesPushButton.clicked()

        self.delayDisplay("Calculate angle")
        q3dcWidget.angleGroupBox.collapsed = False
        q3dcWidget.fidListComboBoxline1LA.setCurrentNode(movingMarkupsFiducial)
        q3dcWidget.fidListComboBoxline1LB.setCurrentNode(movingMarkupsFiducial)
        q3dcWidget.fidListComboBoxline2LA.setCurrentNode(movingMarkupsFiducial)
        q3dcWidget.fidListComboBoxline2LB.setCurrentNode(movingMarkupsFiducial)
        q3dcWidget.line1LAComboBox.currentIndex = 0
        q3dcWidget.line1LBComboBox.currentIndex = 1
        q3dcWidget.line2LAComboBox.currentIndex = 2
        q3dcWidget.line2LBComboBox.currentIndex = 3

        q3dcWidget.pitchCheckBox.checked = True
        q3dcWidget.rollCheckBox.checked = True
        q3dcWidget.yawCheckBox.checked = True

        q3dcWidget.computeAnglesPushButton.clicked()

        self.delayDisplay("Calculate a distance between a line and a point")
        q3dcWidget.angleGroupBox.collapsed = False
        q3dcWidget.fidListComboBoxlineLA.setCurrentNode(movingMarkupsFiducial)
        q3dcWidget.fidListComboBoxlineLB.setCurrentNode(movingMarkupsFiducial)
        q3dcWidget.fidListComboBoxlinePoint.setCurrentNode(movingMarkupsFiducial)
        q3dcWidget.lineLAComboBox.currentIndex = 0
        q3dcWidget.lineLBComboBox.currentIndex = 1
        q3dcWidget.linePointComboBox.currentIndex = 2

        q3dcWidget.landmarkComboBox.setCurrentIndex(0)
        self.delayDisplay("Move endpoint, should update midpoint")
        midpointMarkupIndex = movingMarkupsFiducial.GetNthControlPointIndexByID(midpointMarkupID)
        initialPosition = [0,]*3
        movingMarkupsFiducial.GetNthFiducialPosition(midpointMarkupIndex, initialPosition)
        movingMarkupsFiducial.SetNthFiducialPosition(0, 45, 20, -15)
        movedPosition = [0,]*3
        movingMarkupsFiducial.GetNthFiducialPosition(midpointMarkupIndex, movedPosition)
        if initialPosition == movedPosition:
            print('midpoint landmark did not move')
            return False

        return True
