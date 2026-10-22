      *================================================================*
      * ACCTCONV - build the ACCTREC data set from a text file.        *
      * Text layout (182 bytes per line):                              *
      *   1- 8 account number        9- 19 limit   +9999999.99         *
      *  20-30 balance +9999999.99  31-182 names, address, comments    *
      * Output: fixed 170-byte records, limit/balance packed (COMP-3), *
      * the layout read by the report programs.                        *
      * DD: TXTFILE (input text), ACCTREC (output)                     *
      *================================================================*
       IDENTIFICATION DIVISION.
       PROGRAM-ID. ACCTCONV.
       ENVIRONMENT DIVISION.
       INPUT-OUTPUT SECTION.
       FILE-CONTROL.
           SELECT TXT-FILE ASSIGN TO TXTFILE
                  ORGANIZATION IS LINE SEQUENTIAL
                  FILE STATUS  IS TXT-STATUS.
           SELECT ACCT-REC ASSIGN TO ACCTREC
                  FILE STATUS  IS ACCT-STATUS.
       DATA DIVISION.
       FILE SECTION.
       FD  TXT-FILE.
       01  TXT-REC.
           05  TXT-ACCT-NO            PIC X(8).
           05  TXT-ACCT-LIMIT         PIC +9(7).99.
           05  TXT-ACCT-BALANCE       PIC +9(7).99.
           05  TXT-REST               PIC X(152).
       FD  ACCT-REC RECORDING MODE F.
       01  ACCT-FIELDS.
           05  ACCT-NO                PIC X(8).
           05  ACCT-LIMIT             PIC S9(7)V99 COMP-3.
           05  ACCT-BALANCE           PIC S9(7)V99 COMP-3.
           05  ACCT-REST              PIC X(152).
       WORKING-STORAGE SECTION.
       01  TXT-STATUS                 PIC X(02).
       01  ACCT-STATUS                PIC X(02).
       01  WS-EOF                     PIC X VALUE 'N'.
       PROCEDURE DIVISION.
           OPEN INPUT TXT-FILE
           IF TXT-STATUS NOT = '00'
              DISPLAY 'ACCTCONV: OPEN TXTFILE FAILED ' TXT-STATUS
              MOVE 16 TO RETURN-CODE
              STOP RUN
           END-IF
           OPEN OUTPUT ACCT-REC
           PERFORM UNTIL WS-EOF = 'Y'
              READ TXT-FILE
                 AT END MOVE 'Y' TO WS-EOF
                 NOT AT END
                    MOVE TXT-ACCT-NO      TO ACCT-NO
                    MOVE TXT-ACCT-LIMIT   TO ACCT-LIMIT
                    MOVE TXT-ACCT-BALANCE TO ACCT-BALANCE
                    MOVE TXT-REST         TO ACCT-REST
                    WRITE ACCT-FIELDS
              END-READ
           END-PERFORM
           CLOSE TXT-FILE ACCT-REC
           STOP RUN.
