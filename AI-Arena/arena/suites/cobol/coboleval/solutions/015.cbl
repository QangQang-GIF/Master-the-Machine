       01 WS-I PIC S9(10).
       01 WS-PTR PIC 9(4).
       01 WS-NUM-ED PIC Z(9)9.

       PROCEDURE DIVISION USING LINKED-ITEMS.
           MOVE SPACES TO RESULT
           MOVE 1 TO WS-PTR
           PERFORM VARYING WS-I FROM 0 BY 1 UNTIL WS-I > L-N
               MOVE WS-I TO WS-NUM-ED
               IF WS-I > 0
                   STRING " " DELIMITED BY SIZE
                       INTO RESULT WITH POINTER WS-PTR
               END-IF
               STRING FUNCTION TRIM(WS-NUM-ED) DELIMITED BY SIZE
                   INTO RESULT WITH POINTER WS-PTR
           END-PERFORM
           GOBACK.
       END PROGRAM STRING-SEQUENCE.
