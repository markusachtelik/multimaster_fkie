cmake_minimum_required(VERSION 2.8.3)
project(master_discovery_fkie)

find_package(catkin REQUIRED COMPONENTS multimaster_msgs_fkie)

catkin_python_setup()

catkin_package(CATKIN_DEPENDS)

install(
    PROGRAMS 
        nodes/master_discovery
        nodes/zeroconf
    DESTINATION ${CATKIN_PACKAGE_BIN_DESTINATION}
    )
