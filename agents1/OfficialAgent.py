import sys, random, enum, ast, time, csv
import numpy as np
from matrx import grid_world
from brains1.ArtificialBrain import ArtificialBrain
from actions1.CustomActions import *
from matrx import utils
from matrx.grid_world import GridWorld
from matrx.agents.agent_utils.state import State
from matrx.agents.agent_utils.navigator import Navigator
from matrx.agents.agent_utils.state_tracker import StateTracker
from matrx.actions.door_actions import OpenDoorAction
from matrx.actions.object_actions import GrabObject, DropObject, RemoveObject
from matrx.actions.move_actions import MoveNorth
from matrx.messages.message import Message
from matrx.messages.message_manager import MessageManager
from actions1.CustomActions import RemoveObjectTogether, CarryObjectTogether, DropObjectTogether, CarryObject, Drop

class Phase(enum.Enum):
    INTRO = 1,
    FIND_NEXT_GOAL = 2,
    PICK_UNSEARCHED_ROOM = 3,
    PLAN_PATH_TO_ROOM = 4,
    FOLLOW_PATH_TO_ROOM = 5,
    PLAN_ROOM_SEARCH_PATH = 6,
    FOLLOW_ROOM_SEARCH_PATH = 7,
    PLAN_PATH_TO_VICTIM = 8,
    FOLLOW_PATH_TO_VICTIM = 9,
    TAKE_VICTIM = 10,
    PLAN_PATH_TO_DROPPOINT = 11,
    FOLLOW_PATH_TO_DROPPOINT = 12,
    DROP_VICTIM = 13,
    WAIT_FOR_HUMAN = 14,
    WAIT_AT_ZONE = 15,
    FIX_ORDER_GRAB = 16,
    FIX_ORDER_DROP = 17,
    REMOVE_OBSTACLE_IF_NEEDED = 18,
    ENTER_ROOM = 19

class BaselineAgent(ArtificialBrain):
    def __init__(self, slowdown, condition, name, folder):
        super().__init__(slowdown, condition, name, folder)
        # Initialization of some relevant variables
        self._slowdown = slowdown
        self._condition = condition
        self._humanName = name
        self._folder = folder
        self._phase = Phase.INTRO
        self._roomVics = []
        self._searchedRooms = []
        self._foundVictims = []
        self._collectedVictims = []
        self._foundVictimLocs = {}
        self._sendMessages = []
        self._currentDoor = None
        self._teamMembers = []
        self._carryingTogether = False
        self._remove = False
        self._goalVic = None
        self._goalLoc = None
        self._humanLoc = None
        self._distanceHuman = None
        self._distanceDrop = None
        self._agentLoc = None
        self._todo = []
        self._answered = False
        self._tosearch = []
        self._carrying = False
        self._waiting = False
        self._rescue = None
        self._recentVic = None
        self._receivedMessages = []
        self._moving = False
        self._checkedMessages = []

        # New stores
        self._processedMessages = []
        self._humanSearchedRooms = []
        self._humanFoundVictims = []
        self._checkingSearch = False
        self._checkingCritVic = False
        self._checkingMildVic = False
        self._checkingRemove = False
        self._currentCheckVic = ""
        self._trustBeliefs = {}
        self._currentCheckCollect = ""
        self._checkingMildCollect = False
        self._lieFactor = 1
        self._beliefsLoaded = False
        self._decreasedCompetenceCarryTogether = False

    def initialize(self):
        # Initialization of the state tracker and navigation algorithm
        self._state_tracker = StateTracker(agent_id=self.agent_id)
        self._navigator = Navigator(agent_id=self.agent_id,action_set=self.action_set, algorithm=Navigator.A_STAR_ALGORITHM)

    def filter_observations(self, state):
        # Filtering of the world state before deciding on an action 
        return state

    def decide_on_actions(self, state):
        # Identify team members
        agent_name = state[self.agent_id]['obj_id']
        for member in state['World']['team_members']:
            if member != agent_name and member not in self._teamMembers:
                self._teamMembers.append(member)
        # Create a list of received messages from the human team member
        for mssg in self.received_messages:
            for member in self._teamMembers:
                if mssg.from_id == member and mssg.content not in self._receivedMessages:
                    self._receivedMessages.append(mssg.content)
        # Process messages from team members
        self._processMessages(state, self._teamMembers, self._condition)
        # Initialize and update trust beliefs for team members
        if not self._beliefsLoaded:
            trustBeliefs = self._loadBelief(self._teamMembers, self._folder)
            self._trustBeliefs = trustBeliefs
            self._beliefsLoaded = True

        # Check whether human is close in distance
        # state[{is_human_agent: True}] checks if the human is in view of the rescuebot
        if state[{'is_human_agent': True}]:
            self._distanceHuman = 'close'
        if not state[{'is_human_agent': True}]:
            # Define distance between human and agent based on last known area locations
            if self._agentLoc in [1, 2, 3, 4, 5, 6, 7] and self._humanLoc in [8, 9, 10, 11, 12, 13, 14]:
                self._distanceHuman = 'far'
            if self._agentLoc in [1, 2, 3, 4, 5, 6, 7] and self._humanLoc in [1, 2, 3, 4, 5, 6, 7]:
                self._distanceHuman = 'close'
            if self._agentLoc in [8, 9, 10, 11, 12, 13, 14] and self._humanLoc in [1, 2, 3, 4, 5, 6, 7]:
                self._distanceHuman = 'far'
            if self._agentLoc in [8, 9, 10, 11, 12, 13, 14] and self._humanLoc in [8, 9, 10, 11, 12, 13, 14]:
                self._distanceHuman = 'close'

        # Define distance to drop zone based on last known area location
        if self._agentLoc in [1, 2, 5, 6, 8, 9, 11, 12]:
            self._distanceDrop = 'far'
        if self._agentLoc in [3, 4, 7, 10, 13, 14]:
            self._distanceDrop = 'close'

        # Check whether victims are currently being carried together by human and agent 
        for info in state.values():
            if 'is_human_agent' in info and self._humanName in info['name'] and len(info['is_carrying']) > 0 and 'critical' in info['is_carrying'][0]['obj_id'] or \
                'is_human_agent' in info and self._humanName in info['name'] and len(info['is_carrying']) > 0 and 'mild' in info['is_carrying'][0]['obj_id'] and self._rescue=='together' and not self._moving:
                # If victim is being carried, add to collected victims memory
                if info['is_carrying'][0]['img_name'][8:-4] not in self._collectedVictims:
                    self._collectedVictims.append(info['is_carrying'][0]['img_name'][8:-4])
                self._carryingTogether = True
            if 'is_human_agent' in info and self._humanName in info['name'] and len(info['is_carrying']) == 0:
                self._carryingTogether = False
                self._decreasedCompetenceCarryTogether = False
        # If carrying a victim together, let agent be idle (because joint actions are essentially carried out by the human). Also decrease competence as human didn't carry victim alone
        if self._carryingTogether == True:
            if not self._decreasedCompetenceCarryTogether:
                self._changeCompetence(False)
                self._decreasedCompetenceCarryTogether = True
            return None, {}

        # Send the hidden score message for displaying and logging the score during the task, DO NOT REMOVE THIS
        self._sendMessage('Our score is ' + str(state['rescuebot']['score']) + '.', 'RescueBot')

        # Ongoing loop untill the task is terminated, using different phases for defining the agent's behavior
        while True:
            # Get the competence and willigness values of the human agent
            human_competence = self._trustBeliefs[self._teamMembers[-1]]['competence']
            human_willingness = self._trustBeliefs[self._teamMembers[-1]]['willingness']

            if Phase.INTRO == self._phase:
                # Send introduction message
                self._sendMessage('Hello! My name is RescueBot. Together we will collaborate and try to search and rescue the 8 victims on our right as quickly as possible. \
                Each critical victim (critically injured girl/critically injured elderly woman/critically injured man/critically injured dog) adds 6 points to our score, \
                each mild victim (mildly injured boy/mildly injured elderly man/mildly injured woman/mildly injured cat) 3 points. \
                If you are ready to begin our mission, you can simply start moving.', 'RescueBot')
                # Wait untill the human starts moving before going to the next phase, otherwise remain idle
                if not state[{'is_human_agent': True}]:
                    self._phase = Phase.FIND_NEXT_GOAL
                else:
                    return None, {}

            if Phase.FIND_NEXT_GOAL == self._phase:
                # Definition of some relevant variables
                self._answered = False
                self._goalVic = None
                self._goalLoc = None
                self._rescue = None
                self._moving = True
                remainingZones = []
                remainingVics = []
                remaining = {}
                # Identification of the location of the drop zones
                zones = self._getDropZones(state)
                # Identification of which victims still need to be rescued and on which location they should be dropped
                for info in zones:
                    if str(info['img_name'])[8:-4] not in self._collectedVictims:
                        remainingZones.append(info)
                        remainingVics.append(str(info['img_name'])[8:-4])
                        remaining[str(info['img_name'])[8:-4]] = info['location']
                if remainingZones:
                    self._remainingZones = remainingZones
                    self._remaining = remaining
                # Remain idle if there are no victims left to rescue
                if not remainingZones:
                    return None, {}

                # Check which victims can be rescued next because human or agent already found them             
                for vic in remainingVics:
                    # Define a previously found victim as target victim because all areas have been searched
                    if vic in self._foundVictims and vic in self._todo and len(self._searchedRooms)==0:
                        self._goalVic = vic
                        self._goalLoc = remaining[vic]
                        # Move to target victim
                        self._rescue = 'together'
                        self._sendMessage('Moving to ' + self._foundVictimLocs[vic]['room'] + ' to pick up ' + self._goalVic +'. Please come there as well to help me carry ' + self._goalVic + ' to the drop zone.', 'RescueBot')
                        # Plan path to victim because the exact location is known (i.e., the agent found this victim)
                        if 'location' in self._foundVictimLocs[vic].keys():
                            self._phase = Phase.PLAN_PATH_TO_VICTIM
                            return Idle.__name__, {'duration_in_ticks': 25}
                        # Plan path to area because the exact victim location is not known, only the area (i.e., human found this  victim)
                        if 'location' not in self._foundVictimLocs[vic].keys():
                            self._phase = Phase.PLAN_PATH_TO_ROOM
                            return Idle.__name__, {'duration_in_ticks': 25}
                    # Define a previously found victim as target victim
                    if vic in self._foundVictims and vic not in self._todo:
                        self._goalVic = vic
                        self._goalLoc = remaining[vic]
                        # Rescue together when victim is critical or when the human is weak and the victim is mildly injured
                        if 'critical' in vic or 'mild' in vic and self._condition=='weak':
                            self._rescue = 'together'
                        # Rescue alone if the victim is mildly injured and the human not weak
                        if 'mild' in vic and self._condition!='weak':
                            self._rescue = 'alone'
                        # Plan path to victim because the exact location is known (i.e., the agent found this victim)
                        if 'location' in self._foundVictimLocs[vic].keys():
                            self._phase = Phase.PLAN_PATH_TO_VICTIM
                            return Idle.__name__, {'duration_in_ticks': 25}
                        # Plan path to area because the exact victim location is not known, only the area (i.e., human found this  victim)
                        if 'location' not in self._foundVictimLocs[vic].keys():
                            self._phase = Phase.PLAN_PATH_TO_ROOM
                            return Idle.__name__, {'duration_in_ticks': 25}
                    # If there are no target victims found, visit an unsearched area to search for victims
                    if vic not in self._foundVictims or vic in self._foundVictims and vic in self._todo and len(self._searchedRooms)>0:
                        self._phase = Phase.PICK_UNSEARCHED_ROOM

            if Phase.PICK_UNSEARCHED_ROOM == self._phase:
                agent_location = state[self.agent_id]['location']
                # Identify which areas are not explored yet
                unsearchedRooms = [room['room_name'] for room in state.values()
                                   if 'class_inheritance' in room
                                   and 'Door' in room['class_inheritance']
                                   and room['room_name'] not in self._searchedRooms
                                   and room['room_name'] not in self._tosearch]
                # If all areas have been searched but the task is not finished, start searching areas again
                if self._remainingZones and len(unsearchedRooms) == 0:
                    self._tosearch = []
                    self._searchedRooms = []
                    self._sendMessages = []
                    self.received_messages = []
                    self.received_messages_content = []
                    self._sendMessage('Going to re-search all areas.', 'RescueBot')
                    self._phase = Phase.FIND_NEXT_GOAL
                # If there are still areas to search, define which one to search next
                else:
                    # Identify the closest door when the agent did not search any areas yet
                    if self._currentDoor == None:
                        # Find all area entrance locations
                        self._door = state.get_room_doors(self._getClosestRoom(state, unsearchedRooms, agent_location))[0]
                        self._doormat = state.get_room(self._getClosestRoom(state, unsearchedRooms, agent_location))[-1]['doormat']
                        # Workaround for one area because of some bug
                        if self._door['room_name'] == 'area 1':
                            self._doormat = (3, 5)
                        # Plan path to area
                        self._phase = Phase.PLAN_PATH_TO_ROOM
                    # Identify the closest door when the agent just searched another area
                    if self._currentDoor != None:
                        self._door = state.get_room_doors(self._getClosestRoom(state, unsearchedRooms, self._currentDoor))[0]
                        self._doormat = state.get_room(self._getClosestRoom(state, unsearchedRooms, self._currentDoor))[-1]['doormat']
                        if self._door['room_name'] == 'area 1':
                            self._doormat = (3, 5)
                        self._phase = Phase.PLAN_PATH_TO_ROOM

            if Phase.PLAN_PATH_TO_ROOM == self._phase:
                self._navigator.reset_full()
                # Switch to a different area when the human found a victim
                if self._goalVic and self._goalVic in self._foundVictims and 'location' not in self._foundVictimLocs[self._goalVic].keys():
                    self._door = state.get_room_doors(self._foundVictimLocs[self._goalVic]['room'])[0]
                    self._doormat = state.get_room(self._foundVictimLocs[self._goalVic]['room'])[-1]['doormat']
                    if self._door['room_name'] == 'area 1':
                        self._doormat = (3, 5)
                    doorLoc = self._doormat
                # Otherwise plan the route to the previously identified area to search
                else:
                    if self._door['room_name'] == 'area 1':
                        self._doormat = (3, 5)
                    doorLoc = self._doormat
                self._navigator.add_waypoints([doorLoc])
                # Follow the route to the next area to search
                self._phase = Phase.FOLLOW_PATH_TO_ROOM

            if Phase.FOLLOW_PATH_TO_ROOM == self._phase:
                # Find the next victim to rescue if the previously identified target victim was rescued by the human
                if self._goalVic and self._goalVic in self._collectedVictims:
                    print("checking1")
                    self._currentDoor = None
                    self._phase = Phase.FIND_NEXT_GOAL
                # Identify which area to move to because the human found the previously identified target victim
                if self._goalVic and self._goalVic in self._foundVictims and self._door['room_name'] != self._foundVictimLocs[self._goalVic]['room']:
                    print("checking2")
                    self._currentDoor = None
                    self._phase = Phase.FIND_NEXT_GOAL
                # Identify the next area to search if the human already searched the previously identified area
                if self._door['room_name'] in self._searchedRooms and self._goalVic not in self._foundVictims:
                    print(self._goalVic not in self._foundVictims)
                    print(self._door['room_name'] in self._searchedRooms)
                    self._currentDoor = None
                    self._phase = Phase.FIND_NEXT_GOAL
                # Otherwise move to the next area to search
                else:
                    self._state_tracker.update(state)
                    # Explain why the agent is moving to the specific area, either because it containts the current target victim or because it is the closest unsearched area
                    if self._goalVic in self._foundVictims and str(self._door['room_name']) == self._foundVictimLocs[self._goalVic]['room'] and not self._remove:
                        if self._condition=='weak':
                            self._sendMessage('Moving to ' + str(self._door['room_name']) + ' to pick up ' + self._goalVic + ' together with you.', 'RescueBot')
                        else:
                            self._sendMessage('Moving to ' + str(self._door['room_name']) + ' to pick up ' + self._goalVic + '.', 'RescueBot')
                    if self._goalVic not in self._foundVictims and not self._remove or not self._goalVic and not self._remove :
                        self._sendMessage('Moving to ' + str(self._door['room_name']) + ' because it is the closest unsearched area.', 'RescueBot')
                    self._currentDoor = self._door['location']
                    # Retrieve move actions to execute
                    action = self._navigator.get_move_action(self._state_tracker)
                    if action != None:
                        # Remove obstacles blocking the path to the area 
                        for info in state.values():
                            if 'class_inheritance' in info and 'ObstacleObject' in info[
                                'class_inheritance'] and 'stone' in info['obj_id'] and info['location'] not in [(9, 4), (9, 7), (9, 19), (21, 19)]:
                                self._sendMessage('Reaching ' + str(self._door['room_name']) + ' will take a bit longer because I found stones blocking my path.', 'RescueBot')
                                return RemoveObject.__name__, {'object_id': info['obj_id']}
                        return action, {}
                    # Identify and remove obstacles if they are blocking the entrance of the area
                    self._phase = Phase.REMOVE_OBSTACLE_IF_NEEDED

            if Phase.REMOVE_OBSTACLE_IF_NEEDED == self._phase:
                objects = []
                agent_location = state[self.agent_id]['location']
                # Identify which obstacle is blocking the entrance
                for info in state.values():

                    # Check if object blocking is blocking a searched area
                    if 'class_inheritance' in info and 'ObstacleObject' in info['class_inheritance']:
                        if self._door['room_name'] in self._humanSearchedRooms and self._checkingSearch:
                            self._changeWillingness(False)
                            self._checkingSearch = False
                        if self._checkingMildCollect:
                            self._changeWillingness(False)
                            self._checkingMildCollect = False
                            self._currentCheckCollect = ""
                        if self._checkingMildVic and self._goalLoc == self._door['room_name']:
                            self._changeWillingness(False)
                            self._checkingMildVic = False
                            self._currentCheckVic = ""
                        if self._checkingCritVic and self._goalLoc == self._door['room_name']:
                            self._changeWillingness(False)
                            self._checkingCritVic = False
                            self._currentCheckVic = ""

                    # Big rock case:
                    # - RescueBot must work with human to remove big rock
                    # - Human can decide whether to remove it or continue searching
                    if 'class_inheritance' in info and 'ObstacleObject' in info['class_inheritance'] and 'rock' in info['obj_id']:
                        objects.append(info)
                        # Communicate which obstacle is blocking the entrance
                        if self._answered == False and not self._remove and not self._waiting:    
                            # If human is not willing or competent enough to help remove the big rock, decide to continue instead
                            if not (self._isCompetentEnough(human_competence) and self._isWillingEnough(human_willingness)):
                                # self._sendMessage('Found rock blocking ' + str(self._door['room_name']), 'RescueBot')
                                self._answered = True
                                self._waiting = False
                                self._tosearch.append(self._door['room_name'])
                                self._phase = Phase.FIND_NEXT_GOAL
                                return None, {}
                            else:               
                                self._sendMessage('Found rock blocking ' + str(self._door['room_name']) + '. Please decide whether to "Remove" or "Continue" searching. \n \n \
                                    Important features to consider are: \n safe - victims rescued: ' + str(self._collectedVictims) + ' \n explore - areas searched: area ' + str(self._searchedRooms).replace('area ','') + ' \
                                    \n clock - removal time: 5 seconds \n afstand - distance between us: ' + self._distanceHuman ,'RescueBot')
                                self._waiting = True                          
                        # Determine the next area to explore if the human tells the agent not to remove the obstacle
                        if self.received_messages_content and self.received_messages_content[-1] == 'Continue' and not self._remove:
                            self._answered = True
                            self._waiting = False
                            # Add area to the to do list
                            self._tosearch.append(self._door['room_name'])
                            self._phase = Phase.FIND_NEXT_GOAL
                        # Wait for the human to help removing the obstacle and remove the obstacle together
                        if self.received_messages_content and self.received_messages_content[-1] == 'Remove' or self._remove:
                            if not self._remove:
                                self._answered = True
                            # Tell the human to come over and be idle untill human arrives
                            if not state[{'is_human_agent': True}]:
                                self._sendMessage('Please come to ' + str(self._door['room_name']) + ' to remove rock.','RescueBot')
                                return None, {}
                            # Tell the human to remove the obstacle when he/she arrives
                            if state[{'is_human_agent': True}]:
                                self._sendMessage('Lets remove rock blocking ' + str(self._door['room_name']) + '!','RescueBot')
                                return None, {}
                        # Remain idle untill the human communicates what to do with the identified obstacle 
                        else:
                            return None, {}

                    # Tree case:
                    # - RescueBot must remove alone, human cannot help
                    # - Human can decide whether to remove it or continue searching
                    if 'class_inheritance' in info and 'ObstacleObject' in info['class_inheritance'] and 'tree' in info['obj_id']:
                        objects.append(info)
                        # Communicate which obstacle is blocking the entrance
                        if self._answered == False and not self._remove and not self._waiting:
                            self._sendMessage('Found tree blocking  ' + str(self._door['room_name']) + '. Please decide whether to "Remove" or "Continue" searching. \n \n \
                                Important features to consider are: \n safe - victims rescued: ' + str(self._collectedVictims) + '\n explore - areas searched: area ' + str(self._searchedRooms).replace('area ','') + ' \
                                \n clock - removal time: 10 seconds','RescueBot')
                            self._waiting = True
                        # Determine the next area to explore if the human tells the agent not to remove the obstacle
                        if self.received_messages_content and self.received_messages_content[-1] == 'Continue' and not self._remove:
                            self._answered = True
                            self._waiting = False
                            # Add area to the to do list
                            self._tosearch.append(self._door['room_name'])
                            self._phase = Phase.FIND_NEXT_GOAL
                        # Remove the obstacle if the human tells the agent to do so
                        if self.received_messages_content and self.received_messages_content[-1] == 'Remove' or self._remove:
                            if not self._remove:
                                self._answered = True
                                self._waiting = False
                                self._sendMessage('Removing tree blocking ' + str(self._door['room_name']) + '.','RescueBot')
                            if self._remove:
                                self._sendMessage('Removing tree blocking ' + str(self._door['room_name']) + ' because you asked me to.', 'RescueBot')
                            self._phase = Phase.ENTER_ROOM
                            self._remove = False
                            return RemoveObject.__name__, {'object_id': info['obj_id']}
                        # Remain idle untill the human communicates what to do with the identified obstacle
                        else:
                            return None, {}

                    # Small stone case:
                    # - RescueBot can remove alone, but is faster with human assistance
                    # - Human can decide whether to remove it together, let rescuebot remove alone or continue searching
                    # - (!) A weak human cannot remove the small stone alone
                    if 'class_inheritance' in info and 'ObstacleObject' in info['class_inheritance'] and 'stone' in info['obj_id']:
                        objects.append(info)
                        # Communicate which obstacle is blocking the entrance
                        if self._answered == False and not self._remove and not self._waiting:
                            # If human is not willing or competent enough to help remove the small stone:
                            #   - 90% chance for RescueBot to remove small stone itself
                            #   - 10% chance to continue instead
                            if not (self._isCompetentEnough(human_competence) and self._isWillingEnough(human_willingness)):
                                self._answered = True
                                self._waiting = False
                                rnd = random.random()
                                if rnd >= 0.1:
                                    self._sendMessage('Removing stones blocking ' + str(self._door['room_name']) + '.','RescueBot')
                                    self._phase = Phase.ENTER_ROOM
                                    self._remove = False
                                    return RemoveObject.__name__, {'object_id': info['obj_id']}
                                else:
                                    # self._sendMessage('Found stones blocking  ' + str(self._door['room_name']), 'RescueBot')
                                    self._tosearch.append(self._door['room_name'])
                                    self._phase = Phase.FIND_NEXT_GOAL
                                    return None, {}
                            else:
                                self._sendMessage('Found stones blocking  ' + str(self._door['room_name']) + '. Please decide whether to "Remove together", "Remove alone", or "Continue" searching. \n \n \
                                    Important features to consider are: \n safe - victims rescued: ' + str(self._collectedVictims) + ' \n explore - areas searched: area ' + str(self._searchedRooms).replace('area','') + ' \
                                    \n clock - removal time together: 3 seconds \n afstand - distance between us: ' + self._distanceHuman + '\n clock - removal time alone: 20 seconds','RescueBot')
                                self._waiting = True
                        # Determine the next area to explore if the human tells the agent not to remove the obstacle          
                        if self.received_messages_content and self.received_messages_content[-1] == 'Continue' and not self._remove:
                            self._answered = True
                            self._waiting = False
                            # Add area to the to do list
                            self._tosearch.append(self._door['room_name'])
                            self._phase = Phase.FIND_NEXT_GOAL
                        # Remove the obstacle alone if the human decides so
                        if self.received_messages_content and self.received_messages_content[-1] == 'Remove alone' and not self._remove:
                            self._answered = True
                            self._waiting = False
                            self._sendMessage('Removing stones blocking ' + str(self._door['room_name']) + '.','RescueBot')
                            self._phase = Phase.ENTER_ROOM
                            self._remove = False
                            return RemoveObject.__name__, {'object_id': info['obj_id']}
                        # Remove the obstacle together if the human decides so
                        if self.received_messages_content and self.received_messages_content[-1] == 'Remove together' or self._remove:
                            if not self._remove:
                                self._answered = True
                            # Tell the human to come over and be idle untill human arrives
                            if not state[{'is_human_agent': True}]:
                                self._sendMessage('Please come to ' + str(self._door['room_name']) + ' to remove stones together.','RescueBot')
                                return None, {}
                            # Tell the human to remove the obstacle when he/she arrives
                            if state[{'is_human_agent': True}]:
                                self._sendMessage('Lets remove stones blocking ' + str(self._door['room_name']) + '!','RescueBot')
                                return None, {}
                        # Remain idle until the human communicates what to do with the identified obstacle
                        else:
                            return None, {}
                # If no obstacles are blocking the entrance, enter the area
                if len(objects) == 0:
                    self._answered = False
                    self._remove = False
                    self._waiting = False
                    self._phase = Phase.ENTER_ROOM

            if Phase.ENTER_ROOM == self._phase:
                self._answered = False
                # If the target victim is rescued by the human, identify the next victim to rescue
                if self._goalVic in self._collectedVictims:
                    self._currentDoor = None
                    self._phase = Phase.FIND_NEXT_GOAL
                # If the target victim is found in a different area, start moving there
                if self._goalVic in self._foundVictims and self._door['room_name'] != self._foundVictimLocs[self._goalVic]['room']:
                    self._currentDoor = None
                    self._phase = Phase.FIND_NEXT_GOAL
                # If the human searched the same area, plan searching another area instead
                if self._door['room_name'] in self._searchedRooms and self._goalVic not in self._foundVictims:
                    self._currentDoor = None
                    self._phase = Phase.FIND_NEXT_GOAL
                # Otherwise, enter the area and plan to search it
                else:
                    self._state_tracker.update(state)
                    action = self._navigator.get_move_action(self._state_tracker)
                    if action != None:
                        return action, {}
                    self._phase = Phase.PLAN_ROOM_SEARCH_PATH

            if Phase.PLAN_ROOM_SEARCH_PATH == self._phase:
                self._agentLoc = int(self._door['room_name'].split()[-1])
                # Store the locations of all area tiles
                roomTiles = [info['location'] for info in state.values()
                             if 'class_inheritance' in info
                             and 'AreaTile' in info['class_inheritance']
                             and 'room_name' in info
                             and info['room_name'] == self._door['room_name']]
                self._roomtiles = roomTiles
                # Make the plan for searching the area
                self._navigator.reset_full()
                self._navigator.add_waypoints(self._efficientSearch(roomTiles))
                self._roomVics = []
                self._phase = Phase.FOLLOW_ROOM_SEARCH_PATH

            if Phase.FOLLOW_ROOM_SEARCH_PATH == self._phase:
                # Search the area
                self._state_tracker.update(state)
                action = self._navigator.get_move_action(self._state_tracker)
                if action != None:
                    # Identify victims present in the area
                    for info in state.values():
                        if 'class_inheritance' in info and 'CollectableBlock' in info['class_inheritance']:
                            vic = str(info['img_name'][8:-4])
                            # Remember which victim the agent found in this area
                            if vic not in self._roomVics:
                                self._roomVics.append(vic)
                            
                            # If victim found in searched area decrease willingness
                            if vic not in self._foundVictims and self._checkingSearch and (vic[0:7] != "healthy"):
                                self._changeWillingness(False)
                                self._checkingSearch = False

                            if vic in self._foundVictims and 'location' not in self._foundVictimLocs[vic].keys():
                                self._recentVic = vic
                                # If victim is found by human and we were checking that change the trust
                                # Add the exact victim location to the corresponding dictionary
                                self._foundVictimLocs[vic] = {'location': info['location'],'room': self._door['room_name'], 'obj_id': info['obj_id']}
                                if vic == self._goalVic:
                                    # Communicate which victim was found
                                    self._sendMessage('Found ' + vic + ' in ' + self._door['room_name'] + ' because you told me ' + vic + ' was located here.','RescueBot')
                                    # If victim is in the room when checking increase trust
                                    if self._goalVic == self._currentCheckVic and self._checkingMildVic:
                                        self._changeWillingness(True)
                                        self._currentCheckVic = ""
                                        self._checkingMildVic = False
                                        print("increasing trust (Mild)")
                                    if self._goalVic == self._currentCheckVic and self._checkingCritVic:
                                        self._changeWillingness(True)
                                        self._currentCheckVic = ""
                                        self._checkingCritVic = False
                                        print("increasing trust (Crit)")
                                    # Add the area to the list with searched areas
                                    if self._door['room_name'] not in self._searchedRooms:
                                        self._searchedRooms.append(self._door['room_name'])
                                    # Do not continue searching the rest of the area but start planning to rescue the victim
                                    self._phase = Phase.FIND_NEXT_GOAL

                            # Identify injured victim in the area
                            if 'healthy' not in vic and vic not in self._foundVictims:
                                self._recentVic = vic
                                # Add the victim and the location to the corresponding dictionary
                                self._foundVictims.append(vic)
                                self._foundVictimLocs[vic] = {'location': info['location'],'room': self._door['room_name'], 'obj_id': info['obj_id']}
                                # Communicate which victim the agent found and ask the human whether to rescue the victim now or at a later stage

                                # Mildly injured case:
                                # - RescueBot can carry mildly injured human on its own, but is faster with help from human 
                                # - (!) Weak human must work with RescueBot to carry victim
                                # - Human can decide whether to rescue together, alone or continue searching
                                if 'mild' in vic and self._answered == False and not self._waiting:
                                    
                                    # If victim is in the room when checking decrease trust
                                    if self._checkingMildCollect and self._currentCheckCollect == vic:
                                        self._changeWillingness(False)
                                        self._checkingMildCollect = False
                                        self._currentCheckCollect = ""
                                        print("decrease trust")
                                    # If victim is already collected decrease trust in agent
                                    if vic in self._collectedVictims:
                                        self._changeWillingness(False)
                                        self._collectedVictims.remove(vic)
                                    
                                    # If human is not competent or willing enough:
                                    #   - 90% for RescueBot to rescue victim itself
                                    #   - 10% chance to continue instead
                                    if not (self._isCompetentEnough(human_competence) and self._isWillingEnough(human_willingness)):
                                        self._answered = True
                                        self._waiting = False
                                        rnd = random.random()
                                        if rnd >= 0.1:
                                            self._sendMessage('Picking up ' + self._recentVic + ' in ' + self._door['room_name'] + '.','RescueBot')
                                            self._rescue = 'alone'
                                            self._recentVic = None
                                            self._phase = Phase.FIND_NEXT_GOAL
                                            # return Idle.__name__, {'duration_in_ticks': 25}
                                        else:
                                            # self._sendMessage('Found ' + vic + ' in ' + self._door['room_name'], 'RescueBot')
                                            self._todo.append(self._recentVic)
                                            self._recentVic = None
                                            self._phase = Phase.FIND_NEXT_GOAL
                                            # return Idle.__name__, {'duration_in_ticks': 25}
                                    else:
                                        self._sendMessage('Found ' + vic + ' in ' + self._door['room_name'] + '. Please decide whether to "Rescue together", "Rescue alone", or "Continue" searching. \n \n \
                                            Important features to consider are: \n safe - victims rescued: ' + str(self._collectedVictims) + '\n explore - areas searched: area ' + str(self._searchedRooms).replace('area ','') + '\n \
                                            clock - extra time when rescuing alone: 15 seconds \n afstand - distance between us: ' + self._distanceHuman,'RescueBot')
                                        self._waiting = True
                                    
                                # Critically injured case:
                                # - (!) Human must work with RescueBot to carry victim
                                # - Human can decide whether to rescue together, or continue searching
                                if 'critical' in vic and self._answered == False and not self._waiting:

                                    # If human is not competent or willing enough, decide to continue instead
                                    if not (self._isCompetentEnough(human_competence) and self._isWillingEnough(human_willingness)):
                                        # self._sendMessage('Found ' + vic + ' in ' + self._door['room_name'], 'RescueBot')
                                        self._answered = True
                                        self._waiting = False
                                        self._todo.append(self._recentVic)
                                        self._recentVic = None
                                        self._phase = Phase.FIND_NEXT_GOAL
                                    else:
                                        self._sendMessage('Found ' + vic + ' in ' + self._door['room_name'] + '. Please decide whether to "Rescue" or "Continue" searching. \n\n \
                                            Important features to consider are: \n explore - areas searched: area ' + str(self._searchedRooms).replace('area','') + ' \n safe - victims rescued: ' + str(self._collectedVictims) + '\n \
                                            afstand - distance between us: ' + self._distanceHuman,'RescueBot')
                                        self._waiting = True    
                    # Execute move actions to explore the area
                    return action, {}
                # If victim is not in the room when checking increase willingness. Also increase competence as human can rescue mildly injured victimo
                if self._checkingMildCollect and self._goalVic not in self._roomVics:
                    self._foundVictims.append(self._goalVic)
                    self._collectedVictims.append(self._goalVic)
                    self._changeWillingness(True)
                    self._changeCompetence(True)
                    self._checkingMildCollect = False
                    self._currentCheckVic = ""
                    print("increase trust")
                # Communicate that the agent did not find the target victim in the area while the human previously communicated the victim was located here
                if self._goalVic in self._foundVictims:
                    self._sendMessage(self._goalVic + ' not present in ' + str(self._door['room_name']) + ' because I searched the whole area without finding ' + self._goalVic + '.','RescueBot')
                    
                    # Remove the victim location from memory
                    self._foundVictimLocs.pop(self._goalVic, None)
                    self._foundVictims.remove(self._goalVic)
                    self._roomVics = []
                    
                    # Change trust values if we were checking
                    if self._goalVic == self._currentCheckVic and self._checkingMildVic:
                        self._currentCheckVic = ""
                        self._checkingMildVic = False
                        self._changeWillingness(False)
                        print("Decreasing trust level (Mild)")
                    if self._goalVic == self._currentCheckVic and self._checkingCritVic:
                        self._changeWillingness(False)
                        self._currentCheckVic = ""
                        self._checkingCritVic = False
                        print("Decreasing trust (Crit)")
                    # Reset received messages (bug fix)
                    self.received_messages = []
                    self.received_messages_content = []
                # Add the area to the list of searched areas
                if self._door['room_name'] not in self._searchedRooms:
                    self._searchedRooms.append(self._door['room_name'])
                # Make a plan to rescue a found critically injured victim if the human decides so
                if self.received_messages_content and self.received_messages_content[-1] == 'Rescue' and 'critical' in self._recentVic:
                    self._rescue = 'together'
                    self._answered = True
                    self._waiting = False
                    # Tell the human to come over and help carry the critically injured victim
                    if not state[{'is_human_agent': True}]:
                        self._sendMessage('Please come to ' + str(self._door['room_name']) + ' to carry ' + str(self._recentVic) + ' together.', 'RescueBot')
                    # Tell the human to carry the critically injured victim together
                    if state[{'is_human_agent': True}]:
                        self._sendMessage('Lets carry ' + str(self._recentVic) + ' together! Please wait until I moved on top of ' + str(self._recentVic) + '.', 'RescueBot')
                    self._goalVic = self._recentVic
                    self._recentVic = None
                    self._phase = Phase.PLAN_PATH_TO_VICTIM
                # Make a plan to rescue a found mildly injured victim together if the human decides so
                if self.received_messages_content and self.received_messages_content[-1] == 'Rescue together' and 'mild' in self._recentVic:
                    self._rescue = 'together'
                    self._answered = True
                    self._waiting = False
                    # Tell the human to come over and help carry the mildly injured victim
                    if not state[{'is_human_agent': True}]:
                        self._sendMessage('Please come to ' + str(self._door['room_name']) + ' to carry ' + str(self._recentVic) + ' together.', 'RescueBot')
                    # Tell the human to carry the mildly injured victim together
                    if state[{'is_human_agent': True}]:
                        self._sendMessage('Lets carry ' + str(self._recentVic) + ' together! Please wait until I moved on top of ' + str(self._recentVic) + '.', 'RescueBot')
                    self._goalVic = self._recentVic
                    self._recentVic = None
                    self._phase = Phase.PLAN_PATH_TO_VICTIM
                # Make a plan to rescue the mildly injured victim alone if the human decides so, and communicate this to the human
                if self.received_messages_content and self.received_messages_content[-1] == 'Rescue alone' and 'mild' in self._recentVic:
                    self._sendMessage('Picking up ' + self._recentVic + ' in ' + self._door['room_name'] + '.','RescueBot')
                    self._rescue = 'alone'
                    self._answered = True
                    self._waiting = False
                    self._recentVic = None
                    self._phase = Phase.FIND_NEXT_GOAL
                # Continue searching other areas if the human decides so
                if self.received_messages_content and self.received_messages_content[-1] == 'Continue':
                    self._answered = True
                    self._waiting = False
                    self._todo.append(self._recentVic)
                    self._recentVic = None
                    self._phase = Phase.FIND_NEXT_GOAL
                # Remain idle untill the human communicates to the agent what to do with the found victim
                if self.received_messages_content and self._waiting and self.received_messages_content[-1] != 'Rescue' and self.received_messages_content[-1] != 'Continue':
                    return None, {}
                # Find the next area to search when the agent is not waiting for an answer from the human or occupied with rescuing a victim
                if not self._waiting and not self._rescue:
                    self._recentVic = None
                    self._phase = Phase.FIND_NEXT_GOAL
                # Stop checking search
                if self._checkingSearch:
                    print("Didn't find anything wrong")
                    self._changeWillingness(True)
                self._checkingSearch = False
                return Idle.__name__, {'duration_in_ticks': 25}

            if Phase.PLAN_PATH_TO_VICTIM == self._phase:
                # Plan the path to a found victim using its location
                self._navigator.reset_full()
                self._navigator.add_waypoints([self._foundVictimLocs[self._goalVic]['location']])
                # Follow the path to the found victim
                self._phase = Phase.FOLLOW_PATH_TO_VICTIM

            if Phase.FOLLOW_PATH_TO_VICTIM == self._phase:
                # Start searching for other victims if the human already rescued the target victim
                if self._goalVic and self._goalVic in self._collectedVictims:
                    self._phase = Phase.FIND_NEXT_GOAL
                # Otherwise, move towards the location of the found victim
                else:
                    self._state_tracker.update(state)
                    action = self._navigator.get_move_action(self._state_tracker)
                    if action != None:
                        return action, {}
                    self._phase = Phase.TAKE_VICTIM

            if Phase.TAKE_VICTIM == self._phase:
                # Store all area tiles in a list
                roomTiles = [info['location'] for info in state.values()
                             if 'class_inheritance' in info
                             and 'AreaTile' in info['class_inheritance']
                             and 'room_name' in info
                             and info['room_name'] == self._foundVictimLocs[self._goalVic]['room']]
                self._roomtiles = roomTiles
                objects = []
                # When the victim has to be carried by human and agent together, check whether human has arrived at the victim's location
                for info in state.values():
                    # When the victim has to be carried by human and agent together, check whether human has arrived at the victim's location
                    if 'class_inheritance' in info and 'CollectableBlock' in info['class_inheritance'] and 'critical' in info['obj_id'] and info['location'] in self._roomtiles or \
                        'class_inheritance' in info and 'CollectableBlock' in info['class_inheritance'] and 'mild' in info['obj_id'] and info['location'] in self._roomtiles and self._rescue=='together' or \
                        self._goalVic in self._foundVictims and self._goalVic in self._todo and len(self._searchedRooms)==0 and 'class_inheritance' in info and 'CollectableBlock' in info['class_inheritance'] and 'critical' in info['obj_id'] and info['location'] in self._roomtiles or \
                        self._goalVic in self._foundVictims and self._goalVic in self._todo and len(self._searchedRooms)==0 and 'class_inheritance' in info and 'CollectableBlock' in info['class_inheritance'] and 'mild' in info['obj_id'] and info['location'] in self._roomtiles:
                        objects.append(info)
                        # Remain idle when the human has not arrived at the location
                        if not self._humanName in info['name']:
                            self._waiting = True
                            self._moving = False
                            return None, {}
                # Add the victim to the list of rescued victims when it has been picked up
                if len(objects) == 0 and 'critical' in self._goalVic or len(objects) == 0 and 'mild' in self._goalVic and self._rescue=='together':
                    self._waiting = False
                    if self._goalVic not in self._collectedVictims:
                        self._collectedVictims.append(self._goalVic)
                    self._carryingTogether = True
                    # Determine the next victim to rescue or search
                    self._phase = Phase.FIND_NEXT_GOAL
                # When rescuing mildly injured victims alone, pick the victim up and plan the path to the drop zone
                if 'mild' in self._goalVic and self._rescue=='alone':
                    self._phase = Phase.PLAN_PATH_TO_DROPPOINT
                    if self._goalVic not in self._collectedVictims:
                        self._collectedVictims.append(self._goalVic)
                    self._carrying = True
                    return CarryObject.__name__, {'object_id': self._foundVictimLocs[self._goalVic]['obj_id'], 'human_name':self._humanName}

            if Phase.PLAN_PATH_TO_DROPPOINT == self._phase:
                self._navigator.reset_full()
                # Plan the path to the drop zone
                self._navigator.add_waypoints([self._goalLoc])
                # Follow the path to the drop zone
                self._phase = Phase.FOLLOW_PATH_TO_DROPPOINT

            if Phase.FOLLOW_PATH_TO_DROPPOINT == self._phase:
                # Communicate that the agent is transporting a mildly injured victim alone to the drop zone
                if 'mild' in self._goalVic and self._rescue=='alone':
                    self._sendMessage('Transporting ' + self._goalVic + ' to the drop zone.', 'RescueBot')
                self._state_tracker.update(state)
                # Follow the path to the drop zone
                action = self._navigator.get_move_action(self._state_tracker)
                if action != None:
                    return action, {}
                # Drop the victim at the drop zone
                self._phase = Phase.DROP_VICTIM

            if Phase.DROP_VICTIM == self._phase:
                # Communicate that the agent delivered a mildly injured victim alone to the drop zone
                if 'mild' in self._goalVic and self._rescue=='alone':
                    self._sendMessage('Delivered ' + self._goalVic + ' at the drop zone.', 'RescueBot')
                # Identify the next target victim to rescue
                self._phase = Phase.FIND_NEXT_GOAL
                self._rescue = None
                self._currentDoor = None
                self._tick = state['World']['nr_ticks']
                self._carrying = False
                # Drop the victim on the correct location on the drop zone
                return Drop.__name__, {'human_name': self._humanName}

    def _getDropZones(self, state):
        '''
        @return list of drop zones (their full dict), in order (the first one is the
        the place that requires the first drop)
        '''
        places = state[{'is_goal_block': True}]
        places.sort(key=lambda info: info['location'][1])
        zones = []
        for place in places:
            if place['drop_zone_nr'] == 0:
                zones.append(place)
        return zones

    def _processMessages(self, state, teamMembers, condition):
        '''
        process incoming messages received from the team members
        '''


        receivedMessages = {}
        # Create a dictionary with a list of received messages from each team member
        for member in teamMembers:
            receivedMessages[member] = []
        for mssg in self.received_messages:
            for member in teamMembers:
                if mssg.from_id == member and mssg not in self._processedMessages:
                    receivedMessages[member].append(mssg.content)
                    # Add message to processed messages
                    self._processedMessages.append(mssg)
        # Check the content of the received messages
        for mssgs in receivedMessages.values():
            for msg in mssgs:
                # If a received message involves team members searching areas, add these areas to the memory of areas that have been explored
                if msg.startswith("Search:"):
                    area = 'area ' + msg.split()[-1]
                    if area not in self._searchedRooms:
                        self._searchedRooms.append(area)
                    # Add area to human searched areas
                    if area not in self._humanSearchedRooms:
                        self._humanSearchedRooms.append(area)

                # If a received message involves team members finding victims, add these victims and their locations to memory
                if msg.startswith("Found:"):
                    # Identify which victim and area it concerns
                    if len(msg.split()) == 6:
                        foundVic = ' '.join(msg.split()[1:4])
                    else:
                        foundVic = ' '.join(msg.split()[1:5])
                    loc = 'area ' + msg.split()[-1]
                    # Add the area to the memory of searched areas
                    if loc not in self._searchedRooms:
                        self._searchedRooms.append(loc)
                    # Add area to memore of human searched areas
                    if loc not in self._humanSearchedRooms:
                        self._humanSearchedRooms.append(loc)
                    # Add area to human found victims
                    if loc not in self._humanFoundVictims:
                        self._humanFoundVictims.append({"location": loc, "human": foundVic})
                    # Add the victim and its location to memory
                    if foundVic not in self._foundVictims:
                        self._foundVictims.append(foundVic)
                        self._foundVictimLocs[foundVic] = {'room': loc}
                    if foundVic in self._foundVictims and self._foundVictimLocs[foundVic]['room'] != loc:
                        self._foundVictimLocs[foundVic] = {'room': loc}
                    # Decide to help the human carry a found victim when the human's condition is 'weak'
                    if condition=='weak':
                        self._rescue = 'together'
                    elif 'critically' in foundVic:
                        self._rescue = 'together'
                    # Add the found victim to the to do list when the human's condition is not 'weak'
                    if 'mild' in foundVic and condition!='weak':
                        self._todo.append(foundVic)

                # If a received message involves team members asking for help with removing obstacles, add their location to memory and come over
                if msg.startswith('Remove:'):
                    # Come over immediately when the agent is not carrying a victim
                    self._changeCompetence(False)
                    if not self._carrying:
                        # Identify at which location the human needs help
                        area = 'area ' + msg.split()[-1]
                        self._door = state.get_room_doors(area)[0]
                        self._doormat = state.get_room(area)[-1]['doormat']
                        if area in self._searchedRooms:
                            self._searchedRooms.remove(area)
                        # Clear received messages (bug fix)
                        self.received_messages = []
                        self.received_messages_content = []
                        self._moving = True
                        self._remove = True
                        if self._waiting and self._recentVic:
                            self._todo.append(self._recentVic)
                        self._waiting = False
                        # Let the human know that the agent is coming over to help
                        self._sendMessage('Moving to ' + str(self._door['room_name']) + ' to help you remove an obstacle.','RescueBot')
                        # Plan the path to the relevant area
                        self._phase = Phase.PLAN_PATH_TO_ROOM
                    # Come over to help after dropping a victim that is currently being carried by the agent
                    else:
                        area = 'area ' + msg.split()[-1]
                        self._sendMessage('Will come to ' + area + ' after dropping ' + self._goalVic + '.','RescueBot')
                
                # Implement prob function for checking action
                probability = 0.5
                if (random.random() < probability):
                    self._checkHumanAction(state, msg)

                # If a received message involves team members rescuing victims, add these victims and their locations to memory
                if msg.startswith('Collect:'):
                    # Identify which victim and area it concerns
                    if len(msg.split()) == 6:
                        collectVic = ' '.join(msg.split()[1:4])
                    else:
                        collectVic = ' '.join(msg.split()[1:5])
                    loc = 'area ' + msg.split()[-1]
                    # Add the area to the memory of searched areas
                    if loc not in self._searchedRooms and not self._checkingMildCollect:
                        self._searchedRooms.append(loc)
                    # Add the victim and location to the memory of found victims when we are not checking
                    if collectVic not in self._foundVictims:
                        if not self._checkingMildCollect and collectVic != self._currentCheckCollect:
                            self._foundVictims.append(collectVic)
                            self._foundVictimLocs[collectVic] = {'room': loc}
                    if collectVic in self._foundVictims and self._foundVictimLocs[collectVic]['room'] != loc:
                        self._foundVictimLocs[collectVic] = {'room': loc}
                    # Add the victim to the memory of rescued victims when the human's condition is not weak and when we are not checking
                    if condition != 'weak' and collectVic not in self._collectedVictims:
                        if not self._checkingCritVic and not self._checkingMildCollect and collectVic != self._currentCheckCollect:
                            self._collectedVictims.append(collectVic)
                    # Decide to help the human carry the victim together when the human's condition is weak
                    if condition == 'weak':
                        self._rescue = 'together'

            # Store the current location of the human in memory
            if mssgs and mssgs[-1].split()[-1] in ['1', '2', '3', '4', '5', '6', '7', '8', '9', '10', '11', '12', '13', '14']:
                self._humanLoc = int(mssgs[-1].split()[-1])

    def _loadBelief(self, members, folder):
        '''
        Loads trust belief values if agent already collaborated with human before, otherwise trust belief values are initialized using default values.
        '''
        # Create a dictionary with trust values for all team members
        trustBeliefs = {}
        # Set a default starting trust value
        default = 0.5
        print("Set default")
        trustfile_header = []
        trustfile_contents = []
        # Check if agent already collaborated with this human before, if yes: load the corresponding trust values, if no: initialize using default trust values
        with open(folder+'/beliefs/allTrustBeliefs.csv') as csvfile:
            reader = csv.reader(csvfile, delimiter=';', quotechar="'")
            for row in reader:
                if trustfile_header==[]:
                    trustfile_header=row
                    continue
                # Retrieve trust values 
                if row and row[0]==self._humanName:
                    name = row[0]
                    competence = float(row[1])
                    willingness = float(row[2])
                    trustBeliefs[name] = {'competence': competence, 'willingness': willingness}
                # Initialize default trust values
                if row and row[0]!=self._humanName:
                    competence = default
                    willingness = default
                    trustBeliefs[self._humanName] = {'competence': competence, 'willingness': willingness}
        return trustBeliefs
    
    def _isWillingEnough(self, willingness):
        # Increment willigness by 1 so it fits between the range [0,2] (no negative willigness)
        adjustedWilligness = willingness + 1
        # Calculate probability
        probability = adjustedWilligness / 2
        return random.random() < probability

    def _isCompetentEnough(self, competence):
        # Increment competence by 1 so it fits between the range [0,2] (no negative competence)
        adjustedCompetence = competence + 1
        # Calculate probability
        probability = adjustedCompetence / 2
        return random.random() < probability

    def _sendMessage(self, mssg, sender):
        '''
        send messages from agent to other team members
        '''
        msg = Message(content=mssg, from_id=sender)
        if msg.content not in self.received_messages_content and 'Our score is' not in msg.content:
            self.send_message(msg)
            self._sendMessages.append(msg.content)
        # Sending the hidden score message (DO NOT REMOVE)
        if 'Our score is' in msg.content:
            self.send_message(msg)

    def _getClosestRoom(self, state, objs, currentDoor):
        '''
        calculate which area is closest to the agent's location
        '''
        agent_location = state[self.agent_id]['location']
        locs = {}
        for obj in objs:
            locs[obj] = state.get_room_doors(obj)[0]['location']
        dists = {}
        for room, loc in locs.items():
            if currentDoor != None:
                dists[room] = utils.get_distance(currentDoor, loc)
            if currentDoor == None:
                dists[room] = utils.get_distance(agent_location, loc)

        return min(dists, key=dists.get)

    def _efficientSearch(self, tiles):
        '''
        efficiently transverse areas instead of moving over every single area tile
        '''
        x = []
        y = []
        for i in tiles:
            if i[0] not in x:
                x.append(i[0])
            if i[1] not in y:
                y.append(i[1])
        locs = []
        for i in range(len(x)):
            if i % 2 == 0:
                locs.append((x[i], min(y)))
            else:
                locs.append((x[i], max(y)))
        return locs

    def _checkHumanAction(self, state, action):
        # This function should only check unprocessed messages
        # Search action
        if 'Search' in action:
            # Implementation elsewhere:
                # Come across obstacle, check if location was supposed to have been searched by human
                # Come across victim, check if location was supposed to have been searched by human

            # Check if previous area has been searched fully by human
            if(len(self._humanSearchedRooms) >= 2):
                # Set checking search to true
                self._checkingSearch = True

                # Go to previously searched room
                if not self._carrying:
                    # Identify at which location the human needs help
                    area = 'area ' + self._humanSearchedRooms[-2].split()[-1]
                    self._door = state.get_room_doors(area)[0]
                    self._doormat = state.get_room(area)[-1]['doormat']
                    if area in self._searchedRooms:
                        self._searchedRooms.remove(area)
                    # Clear received messages (bug fix)
                    self.received_messages = []
                    self.received_messages_content = []
                    self._moving = True
                    self._remove = True
                    if self._waiting and self._recentVic:
                        self._todo.append(self._recentVic)
                    self._waiting = False
                    # Plan the path to the relevant area
                    self._phase = Phase.PLAN_PATH_TO_ROOM
                else:
                    # Robot is carrying something so don't check
                    self._checkingSearch = False
                    self._changeWillingness(True)

                
            print("Check search:" + " action")

        # Found critical action
        if 'Found: critically injured' in action:
            if len(action.split()) == 6:
                foundVic = ' '.join(action.split()[1:4])
            else:
                foundVic = ' '.join(action.split()[1:5])
            loc = 'area ' + action.split()[-1]
            # If victim is already collected change willingness otherwise send robot to the room to check if the victim is there or not
            if foundVic in self._collectedVictims:
                self._changeWillingness(False)
            else:
                self._checkingCritVic = True
                self._currentCheckVic = foundVic
                self._goalVic = foundVic
                self._goalLoc = loc
                self._door = state.get_room_doors(loc)[0]
                self._doormat = state.get_room(loc)[-1]['doormat']
                if loc in self._searchedRooms:
                    self._searchedRooms.remove(loc)
                self.received_messages = []
                self.received_messages_content = []
                self._moving = True
                self._remove = True
                if self._waiting and self._recentVic:
                    self._todo.append(self._recentVic)
                self._waiting = False
                self._phase = Phase.PLAN_PATH_TO_ROOM
            # If victim rescued:
                # Decrease trust
            # Else:
                # Check location and identity when going to injured victim
            print("Check location and identity of injured")

        # Found mildly action
        if 'Found: mildly injured' in action:
            if len(action.split()) == 6:
                foundVic = ' '.join(action.split()[1:4])
            else:
                foundVic = ' '.join(action.split()[1:5])
            loc = 'area ' + action.split()[-1]
            # If victim is already collected change willingness otherwise send robot to the room to check if the victim is there or not
            if foundVic in self._collectedVictims:
                self._changeWillingness(False)
            else:
                self._checkingMildVic = True
                self._currentCheckVic = foundVic
                self._goalVic = foundVic
                self._goalLoc = loc
                self._door = state.get_room_doors(loc)[0]
                self._doormat = state.get_room(loc)[-1]['doormat']
                if loc in self._searchedRooms:
                    self._searchedRooms.remove(loc)
                self.received_messages = []
                self.received_messages_content = []
                self._moving = True
                self._remove = True
                if self._waiting and self._recentVic:
                    self._todo.append(self._recentVic)
                self._waiting = False
                self._phase = Phase.PLAN_PATH_TO_ROOM
            print("Store victim found")

        # Pick up mild victim
        if 'Collect: mildly injured' in action:
            self._checkingMildCollect = True
            if len(action.split()) == 6:
                collectedVic = ' '.join(action.split()[1:4])
            else:
                collectedVic = ' '.join(action.split()[1:5])
            loc = 'area ' + action.split()[-1]
            # If victim is already collected change willingness and competence otherwise send robot to the room to check if the victim is there or not
            if collectedVic in self._collectedVictims[0:-1]:
                self._changeWillingness(False)
                self._changeCompetence(False)
                self._checkingMildCollect = False
                print("decreasing trust")
            else:
                self._currentCheckCollect = collectedVic
                self._goalVic = collectedVic
                self._goalLoc = loc
                self._door = state.get_room_doors(loc)[0]
                self._doormat = state.get_room(loc)[-1]['doormat']
                if loc in self._searchedRooms:
                    self._searchedRooms.remove(loc)
                self.received_messages = []
                self.received_messages_content = []
                self._moving = True
                self._remove = True
                if self._waiting and self._recentVic:
                    self._todo.append(self._recentVic)
                self._waiting = False
                self._phase = Phase.PLAN_PATH_TO_ROOM
            print("Store victim rescued")
        
        return False

    def _changeWillingness(self, trust_human):
        # trust_human: Boolean whether to increase or decrease willingness
        if trust_human:
            self._trustBeliefs[self._teamMembers[-1]]['willingness'] = self._trustBeliefs[self._teamMembers[-1]]['willingness'] + (0.1 * self._lieFactor)
            if self._trustBeliefs[self._teamMembers[-1]]['willingness'] > 1:
                self._trustBeliefs[self._teamMembers[-1]]['willingness'] = 1
            self._lieFactor *= 1.1
            print("Willingness increased by " + str((0.1 * self._lieFactor)))
        else:
            self._trustBeliefs[self._teamMembers[-1]]['willingness'] = self._trustBeliefs[self._teamMembers[-1]]['willingness'] - 0.1
            if self._trustBeliefs[self._teamMembers[-1]]['willingness'] < -1:
                self._trustBeliefs[self._teamMembers[-1]]['willingness'] = -1
            self._lieFactor /= 2
            print("Willingness decreased by 0.1")

        with open(self._folder + '/beliefs/currentTrustBelief.csv', mode='w') as csv_file:
            csv_writer = csv.writer(csv_file, delimiter=';', quotechar='"', quoting=csv.QUOTE_MINIMAL)
            csv_writer.writerow(['name','competence','willingness'])
            csv_writer.writerow([self._humanName,self._trustBeliefs[self._teamMembers[-1]]['competence'],self._trustBeliefs[self._teamMembers[-1]]['willingness']])

    def _changeCompetence(self, human_is_competent):
        # human_is_competent: Boolean whether to increase or decrease competence
        if human_is_competent:
            self._trustBeliefs[self._teamMembers[-1]]['competence'] = self._trustBeliefs[self._teamMembers[-1]]['competence'] + 0.3
            if self._trustBeliefs[self._teamMembers[-1]]['competence'] > 1:
                self._trustBeliefs[self._teamMembers[-1]]['competence'] = 1
            print("Competence increased by 0.2")
        else:
            self._trustBeliefs[self._teamMembers[-1]]['competence'] = self._trustBeliefs[self._teamMembers[-1]]['competence'] - 0.1
            if self._trustBeliefs[self._teamMembers[-1]]['competence'] < -1:
                self._trustBeliefs[self._teamMembers[-1]]['competence'] = -1
            print("Competence decreased by 0.05")

        with open(self._folder + '/beliefs/currentTrustBelief.csv', mode='w') as csv_file:
            csv_writer = csv.writer(csv_file, delimiter=';', quotechar='"', quoting=csv.QUOTE_MINIMAL)
            csv_writer.writerow(['name','competence','willingness'])
            csv_writer.writerow([self._humanName,self._trustBeliefs[self._teamMembers[-1]]['competence'],self._trustBeliefs[self._teamMembers[-1]]['willingness']])


