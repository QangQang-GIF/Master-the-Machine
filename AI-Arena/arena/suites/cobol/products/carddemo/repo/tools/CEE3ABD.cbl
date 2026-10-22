      *================================================================*
      * CEE3ABD - stand-in for the Language Environment abend service.  *
      * Prints the abend code and ends the run unit with RC=16.         *
      *================================================================*
       IDENTIFICATION DIVISION.
       PROGRAM-ID. CEE3ABD.
       DATA DIVISION.
       LINKAGE SECTION.
       01  LS-ABCODE                PIC S9(9) BINARY.
       01  LS-TIMING                PIC S9(9) BINARY.
       PROCEDURE DIVISION USING LS-ABCODE LS-TIMING.
           DISPLAY 'CEE3ABD: ABEND U' LS-ABCODE
           STOP RUN RETURNING 16.
