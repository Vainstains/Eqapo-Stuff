// This config is for my personal Truthear x Crinacle Zero: Blue2 IEMs.
// I removed the filters under the grate as it became clogged and damaged, therefore they now sound super sharp and peaky.
// Furthermore, there is channel imbalance in the upper treble due to mismatched peaks (likely due to the way I replaced the metal grate)
// (Sounds fine from 20Hz to 1kHz)

gain -15.2dB;

// === actual corrections ===
include txczb2-filter-correction.eq;

// === just for taste ===

// add some airiness


// bring up the mids a little
PK @600, 4dB, 0.4;

HP @20, 0.8;
PK @22, 3dB, 0.8;
PK @60, -2dB, 0.9;