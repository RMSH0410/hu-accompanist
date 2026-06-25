Ramus execpted recieve: 
(We compare to multiple good recording)

WE WILL AVERAGE SAMPLE RECORDING UP FOR AN OVERALL SAMPLE RECORDING

Good_recording: \[{notevalue:int(hz), duration:int(s), volumn:int(0\~1), ...}]

User_recording: \[{notevalue:int(hz), duration:int(s), volumn:int(0\~1), ...}]

Rating will be = 
      
      factor1\*abs(good_recording[index][notevalue]-user_recording[index][notevalue]) + factor2\*abs(good_recording[index][duration]-user_recording[index][duration]) + ... + factorsomething\*abs(user_recording[index][duration]-user_recording[index-1][duration]) + factoranotherthing\*abs(user_recording[index][duration]-user_recording[index-2][duration])+ ...

Concerns about rubato:

Check if the tempo is decreasing / increasing in sequence

Next step: learn reinforcement learning


Testing music: Accolay Concerto one in a minor
