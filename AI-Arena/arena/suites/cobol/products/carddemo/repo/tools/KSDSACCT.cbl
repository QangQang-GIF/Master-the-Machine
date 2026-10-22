      *================================================================*
      * KSDSACCT - load/unload the ACCTDATA KSDS                       *
      * (account master)                                               *
      * Replacement for IDCAMS REPRO when running under GnuCOBOL.      *
      * Usage : KSDSACCT LOAD | UNLOAD                                 *
      * DD    : TXTFILE  - flat text file, one record per line         *
      *         KSDSFILE - indexed file                                *
      *================================================================*
       IDENTIFICATION DIVISION.
       PROGRAM-ID. KSDSACCT.
       ENVIRONMENT DIVISION.
       INPUT-OUTPUT SECTION.
       FILE-CONTROL.
           SELECT TXT-FILE ASSIGN TO TXTFILE
                  ORGANIZATION IS LINE SEQUENTIAL
                  FILE STATUS  IS TXT-STATUS.
           SELECT KSDS-FILE ASSIGN TO KSDSFILE
                  ORGANIZATION IS INDEXED
                  ACCESS MODE  IS DYNAMIC
                  RECORD KEY   IS KSDS-KEY
                  FILE STATUS  IS KSDS-STATUS.
       DATA DIVISION.
       FILE SECTION.
       FD  TXT-FILE.
       01  TXT-REC                  PIC X(300).
       FD  KSDS-FILE.
       01  KSDS-REC.
           05  KSDS-KEY             PIC X(11).
           05  FILLER               PIC X(289).
       WORKING-STORAGE SECTION.
       01  TXT-STATUS               PIC X(02).
       01  KSDS-STATUS              PIC X(02).
       01  WS-MODE                  PIC X(10).
       01  WS-EOF                   PIC X     VALUE 'N'.
       01  WS-COUNT                 PIC 9(07) VALUE 0.
       PROCEDURE DIVISION.
           ACCEPT WS-MODE FROM ARGUMENT-VALUE
           EVALUATE WS-MODE
             WHEN 'LOAD'
               PERFORM 1000-LOAD
             WHEN 'UNLOAD'
               PERFORM 2000-UNLOAD
             WHEN OTHER
               DISPLAY 'KSDSACCT: USAGE KSDSACCT LOAD|UNLOAD'
               MOVE 16 TO RETURN-CODE
           END-EVALUATE
           STOP RUN.
       1000-LOAD.
           OPEN INPUT TXT-FILE
           IF TXT-STATUS NOT = '00'
              DISPLAY 'KSDSACCT: OPEN TXTFILE FAILED ' TXT-STATUS
              MOVE 16 TO RETURN-CODE
              STOP RUN
           END-IF
           OPEN OUTPUT KSDS-FILE
           IF KSDS-STATUS NOT = '00'
              DISPLAY 'KSDSACCT: OPEN KSDSFILE FAILED ' KSDS-STATUS
              MOVE 16 TO RETURN-CODE
              STOP RUN
           END-IF
           PERFORM UNTIL WS-EOF = 'Y'
              READ TXT-FILE
                 AT END MOVE 'Y' TO WS-EOF
                 NOT AT END
                    MOVE TXT-REC TO KSDS-REC
                    WRITE KSDS-REC
                    IF KSDS-STATUS NOT = '00'
                       DISPLAY 'KSDSACCT: WRITE FAILED ' KSDS-STATUS
                               ' KEY ' KSDS-KEY
                       MOVE 16 TO RETURN-CODE
                       STOP RUN
                    END-IF
                    ADD 1 TO WS-COUNT
              END-READ
           END-PERFORM
           CLOSE TXT-FILE KSDS-FILE.
       2000-UNLOAD.
           OPEN INPUT KSDS-FILE
           IF KSDS-STATUS NOT = '00'
              DISPLAY 'KSDSACCT: OPEN KSDSFILE FAILED ' KSDS-STATUS
              MOVE 16 TO RETURN-CODE
              STOP RUN
           END-IF
           OPEN OUTPUT TXT-FILE
           PERFORM UNTIL WS-EOF = 'Y'
              READ KSDS-FILE NEXT RECORD
                 AT END MOVE 'Y' TO WS-EOF
                 NOT AT END
                    MOVE KSDS-REC TO TXT-REC
                    WRITE TXT-REC
                    ADD 1 TO WS-COUNT
              END-READ
           END-PERFORM
           CLOSE TXT-FILE KSDS-FILE.
