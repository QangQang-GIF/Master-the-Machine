      *================================================================*
      * PARMRUN - run a batch program that expects a JCL EXEC PARM.     *
      * Builds the z/OS style parameter block (half-word length         *
      * followed by the text) and CALLs the program dynamically.        *
      * Usage : PARMRUN <program> '<parm text>'                         *
      *================================================================*
       IDENTIFICATION DIVISION.
       PROGRAM-ID. PARMRUN.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01  WS-PGM                   PIC X(08) VALUE SPACES.
       01  WS-PARM-TEXT             PIC X(100) VALUE SPACES.
       01  MVS-PARM.
           05  MVS-PARM-LENGTH      PIC S9(04) COMP.
           05  MVS-PARM-DATA        PIC X(100).
       PROCEDURE DIVISION.
           ACCEPT WS-PGM       FROM ARGUMENT-VALUE
           ACCEPT WS-PARM-TEXT FROM ARGUMENT-VALUE
           MOVE WS-PARM-TEXT TO MVS-PARM-DATA
           MOVE 0 TO MVS-PARM-LENGTH
           IF WS-PARM-TEXT NOT = SPACES
              COMPUTE MVS-PARM-LENGTH =
                 FUNCTION LENGTH(FUNCTION TRIM(WS-PARM-TEXT TRAILING))
           END-IF
           CALL WS-PGM USING MVS-PARM
              ON EXCEPTION
                 DISPLAY 'PARMRUN: PROGRAM NOT FOUND ' WS-PGM
                 MOVE 16 TO RETURN-CODE
           END-CALL
           STOP RUN.
