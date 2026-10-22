       01 WS-I PIC 9(4).
       01 WS-K PIC 9(4).

       PROCEDURE DIVISION USING LINKED-ITEMS.
           PERFORM VARYING WS-K FROM 1 BY 1 UNTIL WS-K > 100
               MOVE 0 TO RESULT(WS-K)
           END-PERFORM
           MOVE 0 TO WS-K
           PERFORM VARYING WS-I FROM 1 BY 1 UNTIL WS-I > 5
               IF L-L(WS-I) > 0
                   ADD 1 TO WS-K
                   MOVE L-L(WS-I) TO RESULT(WS-K)
               END-IF
           END-PERFORM
           GOBACK.
       END PROGRAM GET-POSITIVE.
