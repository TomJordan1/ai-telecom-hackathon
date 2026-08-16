import re
from typing import List
from app.core.schemas import MessageChunk

def split_long_messages(messages: List[MessageChunk], max_words: int = 25) -> List[MessageChunk]:
    """
    Toma una lista de MessageChunks y divide aquellos cuyo texto sea demasiado largo
    en múltiples chunks más pequeños, respetando los límites de oraciones.
    Esto evita enviar bloques de texto masivos que saturen al usuario.
    """
    result = []
    
    for chunk in messages:
        if not chunk.text:
            result.append(chunk)
            continue
            
        words = chunk.text.split()
        if len(words) <= max_words:
            result.append(chunk)
            continue
            
        # Si es muy largo, intentamos separar por oraciones usando expresiones regulares.
        # Buscar signos de puntuación de final de oración seguidos por un espacio o fin de cadena.
        sentences = re.split(r'(?<=[.!?])\s+', chunk.text.strip())
        
        current_text = ""
        current_word_count = 0
        
        for sentence in sentences:
            if not sentence:
                continue
                
            sentence_word_count = len(sentence.split())
            
            # Si agregar esta oración supera el límite (y ya tenemos algo acumulado), guardamos el chunk actual
            if current_word_count > 0 and (current_word_count + sentence_word_count) > max_words:
                result.append(MessageChunk(
                    text=current_text.strip(),
                    type=chunk.type,
                    delay_ms=chunk.delay_ms
                ))
                current_text = sentence
                current_word_count = sentence_word_count
            else:
                if current_text:
                    current_text += " " + sentence
                else:
                    current_text = sentence
                current_word_count += sentence_word_count
                
        # Guardar lo que quede pendiente
        if current_text:
            result.append(MessageChunk(
                text=current_text.strip(),
                type=chunk.type,
                delay_ms=chunk.delay_ms
            ))
            
    return result
