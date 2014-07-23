import os
import sys
import vtkAll as vtk
from ddapp import botpy
import math
import time
import types
import functools
import numpy as np

from ddapp import transformUtils
from ddapp import lcmUtils
from ddapp.timercallback import TimerCallback
from ddapp.asynctaskqueue import AsyncTaskQueue
from ddapp import objectmodel as om
from ddapp import visualization as vis
from ddapp import applogic as app
from ddapp.debugVis import DebugData
from ddapp import ikplanner
from ddapp import ioUtils
from ddapp.simpletimer import SimpleTimer
from ddapp.utime import getUtime
from ddapp import robotstate
from ddapp import robotplanlistener
from ddapp import segmentation

import drc as lcmdrc

from PythonQt import QtCore, QtGui


class DrillPlannerDemo(object):

    def __init__(self, robotModel, footstepPlanner, manipPlanner, ikPlanner, handDriver, atlasDriver, multisenseDriver, affordanceFitFunction, sensorJointController, planPlaybackFunction, showPoseFunction):
        self.robotModel = robotModel
        self.footstepPlanner = footstepPlanner
        self.manipPlanner = manipPlanner
        self.ikPlanner = ikPlanner
        self.handDriver = handDriver
        self.atlasDriver = atlasDriver
        self.multisenseDriver = multisenseDriver
        self.affordanceFitFunction = affordanceFitFunction
        self.sensorJointController = sensorJointController
        self.planPlaybackFunction = planPlaybackFunction
        self.showPoseFunction = showPoseFunction
        self.graspingHand = 'left'

        self.planFromCurrentRobotState = True # False for operation, True for development

        # For testing:
        self.visOnly = True
        self.useFootstepPlanner = False

        # For autonomousExecute
        #self.visOnly = False
        #self.useFootstepPlanner = True

        self.userPromptEnabled = True
        self.walkingPlan = None
        self.preGraspPlan = None
        self.graspPlan = None
        self.constraintSet = None

        self.plans = []

        self.drillWallXYZ = [0.45, 0.25, 1.1]
        self.drillWallRPY = [0,0,60]

    def addPlan(self, plan):
        self.plans.append(plan)

    def computeGroundFrame(self, robotModel):
        '''
        Given a robol model, returns a vtkTransform at a position between
        the feet, on the ground, with z-axis up and x-axis aligned with the
        robot pelvis x-axis.
        '''
        t1 = robotModel.getLinkFrame('l_foot')
        t2 = robotModel.getLinkFrame('r_foot')
        pelvisT = robotModel.getLinkFrame('pelvis')

        xaxis = [1.0, 0.0, 0.0]
        pelvisT.TransformVector(xaxis, xaxis)
        xaxis = np.array(xaxis)
        zaxis = np.array([0.0, 0.0, 1.0])
        yaxis = np.cross(zaxis, xaxis)
        yaxis /= np.linalg.norm(yaxis)
        xaxis = np.cross(yaxis, zaxis)

        stancePosition = (np.array(t2.GetPosition()) + np.array(t1.GetPosition())) / 2.0

        footHeight = 0.0811

        t = transformUtils.getTransformFromAxes(xaxis, yaxis, zaxis)
        t.PostMultiply()
        t.Translate(stancePosition)
        t.Translate([0.0, 0.0, -footHeight])

        return t


    def computeDrillFrame(self, robotModel):

        position = [1.5, 0.0, 0.9]
        rpy = [1, 1, 1]

        # drill close to origin
        position = [0.65, 0.4, 0.9]
        rpy = [1, 1, 1]

        t = transformUtils.frameFromPositionAndRPY(position, rpy)
        t.Concatenate(self.computeGroundFrame(robotModel))
        return t


    def computeGraspFrame(self):
        self.computeGraspFrameBarrel()


    def computeGraspFrameRotary(self):

        assert self.drillAffordance

        position = [0.0,-0.18,0.0]
        rpy = [-90,90,0]

        t = transformUtils.frameFromPositionAndRPY(position, rpy)
        t.Concatenate(self.drillFrame.transform)

        self.graspFrame = vis.updateFrame(t, 'grasp frame', parent=self.drillAffordance, visible=False, scale=0.3)


    def computeGraspFrameBarrel(self):

        assert self.drillAffordance

        # for left_base_link
        #position = [-0.12, 0.0, 0.025]
        #rpy = [0, 90, 0]

        # for palm point
        position = [-0.04, 0.0, 0.01]
        rpy = [0, 90, -90]

        t = transformUtils.frameFromPositionAndRPY(position, rpy)
        t.Concatenate(self.drillFrame.transform)

        self.graspFrame = vis.updateFrame(t, 'grasp frame', parent=self.drillAffordance, visible=True, scale=0.2)
        self.sampleGraspFrame = vis.updateFrame(transformUtils.copyFrame(t), 'sample grasp frame 0', parent=self.drillAffordance, visible=False, scale=0.2)

        self.frameSync = vis.FrameSync()
        self.frameSync.addFrame(self.graspFrame)
        self.frameSync.addFrame(self.sampleGraspFrame)
        self.frameSync.addFrame(self.drillFrame)


    def computeDrillTipFrame(self):

        assert self.drillAffordance

        position = [0.18, 0.0, 0.13]
        rpy = [0, 0, 0]
        t = transformUtils.frameFromPositionAndRPY(position, rpy)
        t.Concatenate(self.drillFrame.transform)

        drillTipFrame = vis.updateFrame(t, 'drill tip frame', parent=self.drillAffordance, visible=False, scale=0.2)

        self.frameSync.addFrame(self.drillFrame)
        self.frameSync.addFrame(drillTipFrame)


    def computeStanceFrame(self):

        graspFrame = self.graspFrame.transform

        groundFrame = self.computeGroundFrame(self.robotModel)
        groundHeight = groundFrame.GetPosition()[2]

        graspPosition = np.array(graspFrame.GetPosition())
        graspYAxis = [0.0, 1.0, 0.0]
        graspZAxis = [0.0, 0.0, 1.0]
        graspFrame.TransformVector(graspYAxis, graspYAxis)
        graspFrame.TransformVector(graspZAxis, graspZAxis)

        xaxis = graspYAxis
        #xaxis = graspZAxis
        zaxis = [0, 0, 1]
        yaxis = np.cross(zaxis, xaxis)
        yaxis /= np.linalg.norm(yaxis)
        xaxis = np.cross(yaxis, zaxis)

        graspGroundFrame = transformUtils.getTransformFromAxes(xaxis, yaxis, zaxis)
        graspGroundFrame.PostMultiply()
        graspGroundFrame.Translate(graspPosition[0], graspPosition[1], groundHeight)

        position = [-0.67, -0.4, 0.0]
        rpy = [0, 0, 0]

        t = transformUtils.frameFromPositionAndRPY(position, rpy)
        t.Concatenate(graspGroundFrame)

        self.graspStanceFrame = vis.updateFrame(t, 'grasp stance', parent=self.drillAffordance, visible=False, scale=0.2)

        self.frameSync.addFrame(self.drillFrame)
        self.frameSync.addFrame(self.graspStanceFrame)


    def computeFootstepPlan(self):
        startPose = self.getPlanningStartPose()
        goalFrame = self.graspStanceFrame.transform
        request = self.footstepPlanner.constructFootstepPlanRequest(startPose, goalFrame)
        self.footstepPlan = self.footstepPlanner.sendFootstepPlanRequest(request, waitForResponse=True)


    def computeWalkingPlan(self):
        startPose = self.getPlanningStartPose()
        self.walkingPlan = self.footstepPlanner.sendWalkingPlanRequest(self.footstepPlan, startPose, waitForResponse=True)
        self.addPlan(self.walkingPlan)


    def computeEndPose(self):
        graspLinks = {
            'l_hand' : 'left_base_link',
            'r_hand' : 'right_base_link',
           }
        linkName = graspLinks[self.getEndEffectorLinkName()]
        startPose = self.getEstimatedRobotStatePose()
        self.endPosePlan = self.manipPlanner.sendEndPoseGoal(startPose, linkName, self.graspFrame.transform, waitForResponse=True)
        self.showEndPose()

    def showEndPose(self):
        endPose = robotstate.convertStateMessageToDrakePose(self.endPosePlan)
        self.showPoseFunction(endPose)


    def computePreGraspPose(self):


        if self.planFromCurrentRobotState:
            startPose = self.getEstimatedRobotStatePose()
        else:
            planState = self.walkingPlan.plan[-1]
            startPose = robotstate.convertStateMessageToDrakePose(planState)

        constraintSet = self.ikPlanner.planEndEffectorGoal(startPose, self.graspingHand, self.graspFrame)
        endPose, info = constraintSet.runIk()
        endPose = self.ikPlanner.getMergedPostureFromDatabase(endPose, 'General', 'arm up pregrasp', side=self.graspingHand)

        self.preGraspPlan = self.ikPlanner.computePostureGoal(startPose, endPose)


    def planPostureGoal(self, groupName, poseName, side=None):

        startPose = self.getEstimatedRobotStatePose()
        endPose = self.ikPlanner.getMergedPostureFromDatabase(startPose, groupName, poseName, side=side)
        self.posturePlan = self.ikPlanner.computePostureGoal(startPose, endPose)
        self.planPlaybackFunction([self.posturePlan])


    def getEndEffectorLinkName(self):
        linkMap = {
                      'left' : 'l_hand',
                      'right': 'r_hand'
                  }
        return linkMap[self.graspingHand]

    def computeGraspPlan(self):

        startPose = self.getPlanningStartPose()

        constraintSet = self.ikPlanner.planEndEffectorGoal(startPose, self.graspingHand, self.graspFrame, lockTorso=True)
        endPose, info = constraintSet.runIk()
        self.graspPlan = constraintSet.runIkTraj()

        self.addPlan(self.graspPlan)

    def commitFootstepPlan(self):
        self.footstepPlanner.commitFootstepPlan(self.footstepPlan)

    def commitPreGraspPlan(self):
        self.manipPlanner.commitManipPlan(self.preGraspPlan)

    def commitGraspPlan(self):
        self.manipPlanner.commitManipPlan(self.graspPlan)

    def commitStandPlan(self):
        self.manipPlanner.commitManipPlan(self.standPlan)

    def sendPelvisCrouch(self):
        self.atlasDriver.sendPelvisHeightCommand(0.7)

    def sendPelvisStand(self):
        self.atlasDriver.sendPelvisHeightCommand(0.8)

    def computeStandPlan(self):
        startPose = self.getPlanningStartPose()
        self.standPlan = self.ikPlanner.computeNominalPlan(startPose)
        self.addPlan(self.standPlan)

    def sendOpenHand(self):
        self.handDriver.sendOpen()

    def sendCloseHand(self):
        self.handDriver.sendClose(60)

    def sendNeckPitchLookDown(self):
        self.multisenseDriver.setNeckPitch(40)

    def sendNeckPitchLookForward(self):
        self.multisenseDriver.setNeckPitch(15)


    def waitForAtlasBehaviorAsync(self, behaviorName):
        assert behaviorName in self.atlasDriver.getBehaviorMap().values()
        while self.atlasDriver.getCurrentBehaviorName() != behaviorName:
            yield


    def printAsync(self, s):
        yield
        print s


    def userPrompt(self, message):

        if not self.userPromptEnabled:
            return

        yield
        result = raw_input(message)
        if result != 'y':
            raise Exception('user abort.')


    def delay(self, delayTimeInSeconds):
        yield
        t = SimpleTimer()
        while t.elapsed() < delayTimeInSeconds:
            yield


    def waitForCleanLidarSweepAsync(self):
        currentRevolution = self.multisenseDriver.displayedRevolution
        desiredRevolution = currentRevolution + 2
        while self.multisenseDriver.displayedRevolution < desiredRevolution:
            yield


    def spawnDrillAffordance(self):

        drillFrame = self.computeDrillFrame(self.robotModel)

        folder = om.getOrCreateContainer('affordances')
        drillMesh = segmentation.getDrillBarrelMesh()
        self.drillAffordance = vis.showPolyData(drillMesh, 'drill', color=[0.0, 1.0, 0.0], cls=vis.AffordanceItem, parent=folder)
        self.drillAffordance.actor.SetUserTransform(drillFrame)
        self.drillFrame = vis.showFrame(drillFrame, 'drill frame', parent=self.drillAffordance, visible=False, scale=0.2)

        self.computeGraspFrame()
        self.computeStanceFrame()
        self.computeDrillTipFrame()


    ###############################################################################################
    def spawnDrillWallAffordance(self):
        rightAngleLocation = "bottom left"
        trianglePose = transformUtils.frameFromPositionAndRPY(self.drillWallXYZ, self.drillWallRPY)
        segmentation.createDrillWall(rightAngleLocation, trianglePose)


    def moveDrillToHand(self):
        hand = 'left'
        rotation = 0
        offset = .0
        drillFlip = False
        drillOffset = segmentation.getDrillInHandOffset(rotation, offset, drillFlip)

        # create a drill and put it in the hand:
        segmentation.moveDrillToHand(drillOffset, hand)

        self.drillAffordance = om.findObjectByName('drill')
        self.drillFrame = om.findObjectByName('drill frame')

        #if self.drillAffordance:
        #    self.drillAffordance.publish()


    def addDrillButtonFrame(self):
        buttonXYZ = [ self.drillAffordance.params['button_x'], self.drillAffordance.params['button_y'], self.drillAffordance.params['button_z'] ]
        buttonNormal = np.array([ self.drillAffordance.params['button_nx'], self.drillAffordance.params['button_ny'], self.drillAffordance.params['button_nz'] ])

        yaxis = -buttonNormal
        xaxis = [0, 0, 1]
        zaxis = np.cross(yaxis, xaxis)
        xaxis = np.cross(zaxis, yaxis)
        xaxis /= np.linalg.norm(xaxis)
        zaxis /= np.linalg.norm(zaxis)
        t = transformUtils.getTransformFromAxes(xaxis, yaxis, zaxis)
        t.PostMultiply()
        t.Translate(buttonXYZ)

        #t = transformUtils.frameFromPositionAndRPY(buttonXYZ, buttonRPY)
        t.Concatenate( self.drillFrame.transform)
        self.drillButtonFrame = vis.showFrame(t, 'drill button', parent=self.drillAffordance, visible=True, scale=0.2)


    def computeDrillRaisePowerOnPlan(self):
        startPose = self.getPlanningStartPose()
        endPose = self.ikPlanner.getMergedPostureFromDatabase(startPose, 'drill', 'drill in camera - 2014', side=self.graspingHand)
        raisePlan = self.ikPlanner.computePostureGoal(startPose, endPose)
        self.addPlan(raisePlan)


    def pointerHand(self):
        if (self.graspingHand == 'left'):
            return 'right'
        else:
            return 'left'

    def computePointerRaisePowerOnPlan(self):
        startPose = self.getPlanningStartPose()
        endPose = self.ikPlanner.getMergedPostureFromDatabase(startPose, 'drill', 'drill in camera - 2014 pointer', side=self.pointerHand() )
        raisePlan = self.ikPlanner.computePostureGoal(startPose, endPose)
        self.addPlan(raisePlan)


    def initGazeConstraintSet(self, goalFrame, gazeHand):

        # create constraint set
        startPose = self.getPlanningStartPose()
        startPoseName = 'gaze_plan_start'
        endPoseName = 'gaze_plan_end'
        self.ikPlanner.addPose(startPose, startPoseName)
        self.ikPlanner.addPose(startPose, endPoseName)
        self.constraintSet = ikplanner.ConstraintSet(self.ikPlanner, [], startPoseName, endPoseName)
        self.constraintSet.endPose = startPose

        # add body constraints
        bodyConstraints = self.ikPlanner.createMovingBodyConstraints(startPoseName, lockBase=True, lockBack=False, lockLeftArm=gazeHand=='right', lockRightArm=gazeHand=='left')
        self.constraintSet.constraints.extend(bodyConstraints)

        # add gaze constraint
        self.gazeToHandLinkFrame = self.ikPlanner.newGraspToHandFrame(gazeHand)

        print "mfallon"
        print gazeHand
        print goalFrame
        print self.gazeToHandLinkFrame

        gazeConstraint = self.ikPlanner.createGazeGraspConstraint(gazeHand, goalFrame, self.gazeToHandLinkFrame, coneThresholdDegrees= 0.0)
        self.constraintSet.constraints.insert(0, gazeConstraint)

    def appendPositionConstraintForTargetFrame(self, goalFrame, t, gazeHand):
        positionConstraint, _ = self.ikPlanner.createPositionOrientationGraspConstraints(gazeHand, goalFrame, self.gazeToHandLinkFrame)
        positionConstraint.tspan = [t, t]
        self.constraintSet.constraints.append(positionConstraint)


    def planGazeTrajectory(self):
        self.ikPlanner.ikServer.usePointwise = False
        plan = self.constraintSet.runIkTraj()
        self.addPlan(plan)

    def computePointerPressGaze(self):
        gazeHand = self.pointerHand()
        self.initGazeConstraintSet(self.drillButtonFrame, gazeHand)
        self.appendPositionConstraintForTargetFrame(self.drillButtonFrame, 1, gazeHand)
        #self.ikPlanner.ikServer.maxDegreesPerSecond = self.speedSlow
        self.planGazeTrajectory()
        #self.ikPlanner.ikServer.maxDegreesPerSecond = self.speedHigh


    ###############################################################################################

    def findDrillAffordance(self):
        self.drillAffordance = om.findObjectByName('drill')
        self.drillFrame = om.findObjectByName('drill frame')

    def getEstimatedRobotStatePose(self):
        return self.sensorJointController.getPose('EST_ROBOT_STATE')


    def getPlanningStartPose(self):
        if self.planFromCurrentRobotState:
            return self.getEstimatedRobotStatePose()
        else:
            if self.plans:
                return robotstate.convertStateMessageToDrakePose(self.plans[-1].plan[-1])
            else:
                return self.getEstimatedRobotStatePose()


    def cleanupFootstepPlans(self):
        om.removeFromObjectModel(om.findObjectByName('walking goal'))
        om.removeFromObjectModel(om.findObjectByName('footstep plan'))

    def playNominalPlan(self):
        assert None not in self.plans
        self.planPlaybackFunction(self.plans)


    def playPreGraspPlan(self):
        self.planPlaybackFunction([self.preGraspPlan])

    def playGraspPlan(self):
        self.planPlaybackFunction([self.graspPlan])


    def playStandPlan(self):
        self.planPlaybackFunction([self.standPlan])

    def computeNominalPlan(self):

        self.planFromCurrentRobotState = False

        self.findDrillAffordance()
        self.computeGraspFrame()
        self.computeStanceFrame()
        self.computeFootstepPlan()
        self.computeWalkingPlan()
        self.computePreGraspPose()
        self.computeGraspPlan()
        self.playNominalPlan()


    def computeNominalPlanTurnOn(self):

        self.planFromCurrentRobotState = False

        #self.findDrillAffordance()
        self.moveDrillToHand()
        self.addDrillButtonFrame()
        self.computeDrillRaisePowerOnPlan()
        self.moveDrillToHand()
        self.computePointerRaisePowerOnPlan()
        self.computePointerPressGaze()
        self.computePointerRaisePowerOnPlan()
        self.computeStandPlan()
        self.moveDrillToHand()
        self.playNominalPlan()


    def sendPlanWithHeightMode(self):
        self.atlasDriver.sendPlanUsingBdiHeight(True)

    def autonomousExecute(self):

        self.planFromCurrentRobotState = True

        taskQueue = AsyncTaskQueue()

        # stand and open hand
        taskQueue.addTask(self.userPrompt('stand and open hand. continue? y/n: '))
        taskQueue.addTask(self.atlasDriver.sendStandCommand)
        taskQueue.addTask(self.sendOpenHand)
        taskQueue.addTask(self.sendPlanWithHeightMode)

        # user prompt
        taskQueue.addTask(self.userPrompt('sending neck pitch forward. continue? y/n: '))

        # set neck pitch
        taskQueue.addTask(self.printAsync('neck pitch forward'))
        taskQueue.addTask(self.sendNeckPitchLookForward)
        taskQueue.addTask(self.delay(1.0))

        # user prompt
        taskQueue.addTask(self.userPrompt('perception and fitting. continue? y/n: '))

        # perception & fitting
        taskQueue.addTask(self.printAsync('waiting for clean lidar sweep'))
        taskQueue.addTask(self.waitForCleanLidarSweepAsync)

        taskQueue.addTask(self.printAsync('fitting drill affordance'))
        taskQueue.addTask(self.affordanceFitFunction)
        taskQueue.addTask(self.findDrillAffordance)

        # compute grasp & stance
        taskQueue.addTask(self.printAsync('computing grasp and stance frames'))
        taskQueue.addTask(self.computeGraspFrame)
        taskQueue.addTask(self.computeStanceFrame)

        # footstep plan
        taskQueue.addTask(self.printAsync('compute footstep plan'))
        taskQueue.addTask(self.computeFootstepPlan)

        # user prompt
        taskQueue.addTask(self.userPrompt('sending footstep plan. continue? y/n: '))

        # walk
        taskQueue.addTask(self.printAsync('walking'))
        taskQueue.addTask(self.commitFootstepPlan)
        taskQueue.addTask(self.waitForAtlasBehaviorAsync('step'))
        taskQueue.addTask(self.waitForAtlasBehaviorAsync('stand'))

        # user prompt
        taskQueue.addTask(self.userPrompt('sending neck pitch. continue? y/n: '))

        # set neck pitch
        taskQueue.addTask(self.printAsync('neck pitch down'))
        taskQueue.addTask(self.sendNeckPitchLookDown)
        taskQueue.addTask(self.delay(1.0))

        # user prompt
        #taskQueue.addTask(self.userPrompt('crouch. continue? y/n: '))

        # crouch
        #taskQueue.addTask(self.printAsync('send manip mode'))
        #taskQueue.addTask(self.atlasDriver.sendManipCommand)
        #taskQueue.addTask(self.delay(1.0))
        #taskQueue.addTask(self.printAsync('crouching'))
        #taskQueue.addTask(self.sendPelvisCrouch)
        #taskQueue.addTask(self.delay(3.0))


        # user prompt
        taskQueue.addTask(self.userPrompt('plan pre grasp. continue? y/n: '))


        # compute pre grasp plan
        taskQueue.addTask(self.printAsync('computing pre grasp plan'))
        taskQueue.addTask(self.computePreGraspPose)
        taskQueue.addTask(self.playPreGraspPlan)

        # user prompt
        taskQueue.addTask(self.userPrompt('commit manip plan. continue? y/n: '))

        # commit pre grasp plan
        taskQueue.addTask(self.atlasDriver.sendManipCommand)
        taskQueue.addTask(self.delay(1.0))
        taskQueue.addTask(self.printAsync('commit pre grasp plan'))
        taskQueue.addTask(self.commitPreGraspPlan)
        taskQueue.addTask(self.delay(10.0))


        # user prompt
        taskQueue.addTask(self.userPrompt('perception and fitting. continue? y/n: '))

        # perception & fitting
        taskQueue.addTask(self.printAsync('waiting for clean lidar sweep'))
        taskQueue.addTask(self.waitForCleanLidarSweepAsync)

        taskQueue.addTask(self.printAsync('fitting drill affordance'))
        taskQueue.addTask(self.affordanceFitFunction)
        taskQueue.addTask(self.findDrillAffordance)

        # compute grasp frame
        taskQueue.addTask(self.printAsync('computing grasp frame'))
        taskQueue.addTask(self.computeGraspFrame)


        # compute grasp plan
        taskQueue.addTask(self.printAsync('computing grasp plan'))
        taskQueue.addTask(self.computeGraspPlan)
        taskQueue.addTask(self.playGraspPlan)

        # user prompt
        taskQueue.addTask(self.userPrompt('commit manip plan. continue? y/n: '))

        # commit grasp plan
        taskQueue.addTask(self.printAsync('commit grasp plan'))
        taskQueue.addTask(self.commitGraspPlan)
        taskQueue.addTask(self.delay(10.0))

        # recompute grasp plan
        taskQueue.addTask(self.printAsync('recompute grasp plan'))
        taskQueue.addTask(self.computeGraspPlan)
        taskQueue.addTask(self.playGraspPlan)

        # user prompt
        taskQueue.addTask(self.userPrompt('commit manip plan. continue? y/n: '))

        # commit grasp plan
        taskQueue.addTask(self.printAsync('commit grasp plan'))
        taskQueue.addTask(self.commitGraspPlan)
        taskQueue.addTask(self.delay(3.0))


        # user prompt
        taskQueue.addTask(self.userPrompt('closing hand. continue? y/n: '))

        # close hand
        taskQueue.addTask(self.printAsync('close hand'))
        taskQueue.addTask(self.sendCloseHand)
        taskQueue.addTask(self.delay(3.0))


        taskQueue.addTask(self.userPrompt('send stand command. continue? y/n: '))
        taskQueue.addTask(self.atlasDriver.sendStandCommand)
        taskQueue.addTask(self.delay(5.0))
        taskQueue.addTask(self.atlasDriver.sendManipCommand)
        taskQueue.addTask(self.delay(1.0))

        '''
        # user prompt
        taskQueue.addTask(self.userPrompt('compute stand plan. continue? y/n: '))

        # stand
        taskQueue.addTask(self.computeStandPlan)
        taskQueue.addTask(self.playStandPlan)

        taskQueue.addTask(self.userPrompt('commit stand. continue? y/n: '))

        # compute pre grasp plan
        taskQueue.addTask(self.commitStandPlan)
        taskQueue.addTask(self.delay(10.0))
        '''

        # user prompt
        #taskQueue.addTask(self.userPrompt('commit manip plan. continue? y/n: '))

        # commit pre grasp plan
        #taskQueue.addTask(self.printAsync('commit pre grasp plan'))
        #taskQueue.addTask(self.commitPreGraspPlan)
        #taskQueue.addTask(self.delay(10.0))

        taskQueue.addTask(self.printAsync('done!'))

        return taskQueue


