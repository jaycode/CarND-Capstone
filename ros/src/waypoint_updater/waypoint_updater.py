#!/usr/bin/env python

import rospy
from geometry_msgs.msg import PoseStamped
from styx_msgs.msg import Lane, Waypoint
from std_msgs.msg import Int32
import tf

import math
import PyKDL
from copy import deepcopy

from helpers import mph2mps, mps2mph, distance

import time

# from scipy.interpolate import interp1d

'''
This node will publish waypoints from the car's current position to some `x` distance ahead.

As mentioned in the doc, you should ideally first implement a version which does not care
about traffic lights or obstacles.

Once you have created dbw_node, you will update this node to use the status of traffic lights too.

Please note that our simulator also provides the exact location of traffic lights and their
current status in `/vehicle/traffic_lights` message. You can use this message to build this node
as well as to verify your TL classifier.

TODO (for Yousuf and Aaron): Stopline location for each traffic light.
'''

# Lookahead is the waypoints directly ahead the car. It is used to
# keep the car drives inside a lane.
LOOKAHEAD_WPS = 12

# How far (in number of waypoints) the car may notice a traffic light and/or obstacle.
LINE_OF_SIGHT_WPS = 80

class WaypointUpdater(object):
    def __init__(self):
        rospy.init_node('waypoint_updater')

        rospy.Subscriber('/current_pose', PoseStamped, self.pose_cb)
        rospy.Subscriber('/base_waypoints', Lane, self.waypoints_cb)
        rospy.Subscriber('/traffic_waypoint', Int32, self.traffic_cb)

        self.final_waypoints_pub = rospy.Publisher('/final_waypoints', Lane, queue_size=1)

        self.waypoints = None
        self.waypoints_header = None

        self.redlight_wp = None

        # To avoid publishing same points multiple times.
        self.published_wp = None

        # For benchmarking closest wp code 
        # self.sum_wp_time = 0.0
        # self.count_wp_time = 0

        # Current waypoint id.
        self.cur_wp = None

        # Current car's pose.
        self.cur_pose = None

        self.yaw = 0

        self.config = {
            "v": mph2mps(25),
            "full_v": mph2mps(40)
        }

        self.tl_config = {
            # How far (in meters) before traffic light should we stop?
            # This value can be found by enabling the log that starts with
            # "redlight_visible" below and then manually drive the car
            # while monitoring the value of `rl`
            "offset": 28.28,


            # How far before the stop line should we begin braking?
            "brake_start": 23.0,

            # TODO:
            # When the car is at at least `overshoot` meters behind of 
            # the stop line, go full speed ahead (full_v) since there
            # is no turning back. Negative value means this point is ahead of
            # the line
            # "overshoot": -5.7,

            "brake_v": 0.0,
            "brake_traj": (lambda i, wps: (i / float(len(wps))) * (self.config["v"]))
            # "brake_traj": (lambda i, wps: 0.0)
        }

        self.cnt = 0
        
        rospy.spin()

    def pose_cb(self, msg):
        """

        Note: pose_cb is NOT called after the car stopped.

        msg:

        geometry_msgs/Pose pose
          geometry_msgs/Point position
            float64 x
            float64 y
            float64 z
          geometry_msgs/Quaternion orientation
            float64 x
            float64 y
            float64 z
            float64 w
        """
        self.cnt += 1
        if self.cnt % 10 != 0:
          return

        start_time = time.time()
        
        self.pose = msg.pose
        pos = self.pose.position
        quat = PyKDL.Rotation.Quaternion(self.pose.orientation.x,
                                         self.pose.orientation.y,
                                         self.pose.orientation.z,
                                         self.pose.orientation.w)
        orient = quat.GetRPY()
        self.yaw = orient[2]

        #self.drive()
        elapsed_time = time.time() - start_time
        rospy.loginfo('pose_cb time = %0.1fus\n' % (1000.0*1000*elapsed_time))

    def waypoints_cb(self, waypoints):
        start_time = time.time()
        self.waypoints = waypoints.waypoints
        self.waypoints_header = waypoints.header
        elapsed_time = time.time() - start_time
        rospy.loginfo('waypoints_cb time = %0.1fus\n' % (1000.0*1000*elapsed_time))

    def traffic_cb(self, msg):
        start_time = time.time()
        self.redlight_wp = None
        tl_wp = msg.data
        # `tl_wp >= self.cur_wp` ensures traffic light is in front of the car.
        # rospy.loginfo("tl_wp: {}".format(tl_wp))
        if tl_wp > -1 and tl_wp > self.cur_wp:
            self.redlight_wp = tl_wp
            rospy.loginfo("redlight_wp: {} cur_wp: {}".format(self.redlight_wp, self.cur_wp))
        self.drive()
        elapsed_time = time.time() - start_time
        rospy.loginfo('traffic_cb time = %0.1fus\n' % (1000.0*1000*elapsed_time))

    def obstacle_cb(self, msg):
        # TODO: Callback for /obstacle_waypoint message. We will implement it later
        pass

    def drive(self):
        start_time = time.time()
        if self.waypoints is not None:
            # rospy.loginfo("curyaw: {}".format(yaw))

            # start = time.time()
            self.cur_wp = self.get_closest_waypoint(self.pose)
            # end = time.time()
            # self.sum_wp_time += (end - start)
            # self.count_wp_time += 1
            # avg_wp_time = self.sum_wp_time / self.count_wp_time
            # rospy.loginfo("m_id time: {}".format(avg_wp_time))

            lane = Lane()
            first_wp_obj = self.waypoints[self.cur_wp]

            rl_is_visible = self.redlight_is_visible()
            redlight_wp = self.redlight_wp

            rospy.loginfo("redlight_visible: {}, cur: {}, rl: {}({}m)".format(
                rl_is_visible,
                self.cur_wp,
                redlight_wp,
                None if redlight_wp is None else \
                  (distance(self.waypoints[redlight_wp].pose.pose.position,
                            self.waypoints[self.cur_wp].pose.pose.position))
            ))

            if rl_is_visible:
            # if False:
                # stopline waypoint
                sl_wp = self.dist2wp(redlight_wp, -self.tl_config["offset"])
                wps = self.wps_behind_wp(sl_wp, self.tl_config["brake_start"])
                self.full_brake(wps)
                rospy.loginfo("wps v: {}".format(
                    list(map(lambda x: self.waypoints[x].twist.twist.linear.x, wps))))
                if self.distance_to_wp(sl_wp) > self.tl_config["brake_start"]:
                    self.set_waypoint_velocity(first_wp_obj, self.config["v"])

            else:
                self.set_waypoint_velocity(first_wp_obj, self.config["v"])

            rospy.loginfo("v was set to: {}".format(
                self.waypoints[self.cur_wp].twist.twist.linear.x))
            
            for i, wp in enumerate(self.waypoints[
                self.cur_wp:(self.cur_wp+LOOKAHEAD_WPS)]):
                lane.waypoints.append(wp)

            # rospy.loginfo("(p) next_wp angular: {}".format(lane.waypoints[0].twist.twist.angular))
            self.final_waypoints_pub.publish(lane)
        elapsed_time = time.time() - start_time
        rospy.loginfo('drive() time = %0.1fus\n' % (1000.0*1000*elapsed_time))


    def get_waypoint_velocity(self, waypoint):
        return waypoint.twist.twist.linear.x

    def set_waypoint_velocity(self, waypoint, velocity):
        waypoint.twist.twist.linear.x = velocity

    def wp_distance(self, wp1, wp2):
        """ Get distance between two waypoints.

        The distance is calculated by adding distances sequentially for each
        waypoint pair.

        Args:
            wp1 (int): Index of the first waypoint (must be closest to the car)
            wp2 (int): Index of the last waypoint (must be farthest from the car)

        Returns:
            double: Sum of distances of all waypoints between wp1 and wp2.
        """
        # TODO: Circular path (i.e. wp1 can be > wp2)
        dist = 0
        for i in range(wp1, wp2+1):
            dist += distance(self.waypoints[wp1].pose.pose.position, self.waypoints[i].pose.pose.position)
            wp1 = i
        return dist

    def get_closest_waypoint(self, pose):
        """Identifies the closest path waypoint to the given position
            https://en.wikipedia.org/wiki/Closest_pair_of_points_problem
        Args:
            pose (Pose): position to match a waypoint to
        Returns:
            int: index of the closest waypoint in self.waypoints
        """

        pos = pose.position
        l_id = 0
        r_id = len(self.waypoints) - 1
        m_id = len(self.waypoints)-1

        while l_id < r_id:
            ldist = distance(self.waypoints[l_id].pose.pose.position, pos)
            rdist = distance(self.waypoints[r_id].pose.pose.position, pos)
            xmid = (l_id + r_id) // 2
            mdist = distance(self.waypoints[xmid].pose.pose.position, pos)

            closest_dist = ldist
            m_id = l_id
            if mdist < closest_dist:
                closest_dist = mdist
                m_id = xmid
            if rdist < closest_dist:
                closest_dist = rdist
                m_id = r_id

            # If l_id is right before xmid and xmid is right before r_id,
            # then xmid is the closest waypoint
            if l_id == xmid -1 and xmid == r_id -1:
                break

            # c: car
            # l: left point
            # r: right point
            # m: xmid
            # *: closest waypoint
            if rdist < mdist:
                if ldist < rdist:
                    # l--c----r--m
                    r_id = xmid - 1
                else:
                    # l----c--r--m
                    l_id = xmid + 1

            elif mdist < closest_dist:
                # l--c--m--*--r
                l_id = xmid-1
            elif mdist > closest_dist :
                # l--c--*--m--r
                r_id = xmid+1

            elif mdist == closest_dist:
                # ?-cm-?
                if ldist < rdist:
                    # l--cm---r
                    r_id = xmid + (r_id - xmid) // 2
                elif rdist < ldist:
                    # l---cm--r
                    l_id = xmid - (xmid - l_id) // 2

        return m_id

    def get_waypoint_yaw(self, wp):
        """ Get yaw of a waypoint

        Args:
            wp (Waypoint): A Waypoint object

        Returns:
            float: Yaw of that waypoint object.
        """
        next_x = wp.pose.pose.position.x
        next_y = wp.pose.pose.position.y

        quat = PyKDL.Rotation.Quaternion(wp.pose.pose.orientation.x,
                                         wp.pose.pose.orientation.y,
                                         wp.pose.pose.orientation.z,
                                         wp.pose.pose.orientation.w)
        orient = quat.GetRPY()
        yaw = orient[2]
        return yaw

    def redlight_is_visible(self):
        """ See if a red light is visible from current car's position.

        Returns:
            bool: Visible if True.
        """
        if self.redlight_wp is None:
            return False
        else:
            return ((self.cur_wp + LINE_OF_SIGHT_WPS) >= self.redlight_wp)

    def distance_to_wp(self, wp):
        """ Get a distance from current waypoint to a target waypoint.

        Args:
            wp (int): Target waypoint.
        Returns:
            double: Distance from current waypoint tp target waypoint
        """
        dist = 0.0
        if self.cur_wp > wp:
            dist = -self.wp_distance(wp, self.cur_wp)
        else:
            dist = self.wp_distance(self.cur_wp, wp)
        return dist

    def dist2wp(self, wp, dist):
        """ Find out last waypoint from given current waypoint and a distance.

        Args:
            wp (int): The beginning waypoint id.
            dist (double): Distance from wp, negative for orientation opposite to the car's
                           heading (i.e. lower waypoints).

        Returns:
            int: The last waypoint's id.
        """
        dist_tally = dist
        d = 1
        if dist < 0:
            dist_tally *= -1
            d = -1

        fin_wp = wp
        while dist_tally > 0:
            new_fin_wp = fin_wp + d
            dist = self.wp_distance(new_fin_wp, fin_wp)
            dist_tally -= dist
            fin_wp = new_fin_wp
        return fin_wp

    def wps_behind_wp(self, wp, dist):
        """ Get all waypoints under specific distance behind a waypoint.

        "Behind" means in the direction closer to the car.
        
        Args:
            wp (int): Waypoint id.
            dist (double): Distance from wp.

        Returns:
            list (int): List of waypoint ids.
        """
        start_wp = self.dist2wp(wp, -dist)
        return range(start_wp, (wp + 1))


    def full_brake(self, wps):
        """ Initiate full brake through the given waypoint ids.

        Args:
            wps (list(int)): List of waypoint ids. The car should
                             be at full stop at the last id.
        """
        last_wp = wps[len(wps)-1]
        self.set_waypoint_velocity(self.waypoints[last_wp], 0.)
        waypoints = [self.waypoints[i] for i in wps][:-1]
        waypoints.reverse()
        for i, wp in enumerate(waypoints):
            dist = distance(wp.pose.pose.position,
                            self.waypoints[last_wp].pose.pose.position)
            new_v = self.tl_config["brake_traj"](i, waypoints)
            if new_v < 1.0: new_v = self.tl_config["brake_v"]
            # rospy.loginfo("set v to {} (i = {})".format(new_v, i))
            self.set_waypoint_velocity(
                wp,
                new_v
                )

if __name__ == '__main__':
    try:
        WaypointUpdater()
    except rospy.ROSInterruptException:
        rospy.logerr('Could not start waypoint updater node.')
