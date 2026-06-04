import time
import wujihandpy

hand = wujihandpy.Hand()
try:
    hand.write_joint_enabled(True)
    print("enabled")
    hand.finger(1).joint(0).write_joint_target_position(0.3)
    time.sleep(0.5)
    hand.finger(1).joint(0).write_joint_target_position(0.0)
    time.sleep(0.5)
finally:
    hand.write_joint_enabled(False)
    print("disabled")
