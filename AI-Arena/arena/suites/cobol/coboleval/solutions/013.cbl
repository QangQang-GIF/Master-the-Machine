       01 WS-A PIC S9(10).
       01 WS-B PIC S9(10).
       01 WS-T PIC S9(10).

       PROCEDURE DIVISION USING LINKED-ITEMS.
           MOVE L-A TO WS-A
           MOVE L-B TO WS-B
           PERFORM UNTIL WS-B = 0
               MOVE FUNCTION MOD(WS-A, WS-B) TO WS-T
               MOVE WS-B TO WS-A
               MOVE WS-T TO WS-B
           END-PERFORM
           MOVE WS-A TO RESULT
           GOBACK.
       END PROGRAM GREATEST-COMMON-DIVISOR.
