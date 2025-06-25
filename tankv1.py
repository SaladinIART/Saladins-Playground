import pygame
import sys

pygame.init()
screen = pygame.display.set_mode((800, 600))
pygame.display.set_caption("Tank Game")

clock = pygame.time.Clock()

# Tank properties
tank_color = (0, 255, 0)
tank_width, tank_height = 40, 40
tank_x, tank_y = 380, 280
tank_speed = 5
tank_dir = "UP"  # Track tank direction

# Bullet properties
bullet_color = (255, 255, 0)
bullet_width, bullet_height = 8, 8
bullets = []
bullet_speed = 10

# Wall properties and layout (list of pygame.Rect)
wall_color = (150, 75, 0)
walls = [
    pygame.Rect(50, 50, 700, 20),    # top border
    pygame.Rect(50, 530, 700, 20),   # bottom border
    pygame.Rect(50, 70, 20, 480),    # left border
    pygame.Rect(730, 70, 20, 480),   # right border
    pygame.Rect(350, 250, 100, 100), # center block
]

def tank_rect(x, y):
    return pygame.Rect(x, y, tank_width, tank_height)

running = True
while running:
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False
        # Shoot bullet
        if event.type == pygame.KEYDOWN and event.key == pygame.K_SPACE:
            if tank_dir == "UP":
                bullet_dx, bullet_dy = 0, -bullet_speed
                bullet_x = tank_x + tank_width // 2 - bullet_width // 2
                bullet_y = tank_y
            elif tank_dir == "DOWN":
                bullet_dx, bullet_dy = 0, bullet_speed
                bullet_x = tank_x + tank_width // 2 - bullet_width // 2
                bullet_y = tank_y + tank_height
            elif tank_dir == "LEFT":
                bullet_dx, bullet_dy = -bullet_speed, 0
                bullet_x = tank_x
                bullet_y = tank_y + tank_height // 2 - bullet_height // 2
            else:  # RIGHT
                bullet_dx, bullet_dy = bullet_speed, 0
                bullet_x = tank_x + tank_width
                bullet_y = tank_y + tank_height // 2 - bullet_height // 2
            bullets.append([bullet_x, bullet_y, bullet_dx, bullet_dy])

    # Key press handling with wall collision
    keys = pygame.key.get_pressed()
    next_x, next_y = tank_x, tank_y
    if keys[pygame.K_LEFT]:
        next_x -= tank_speed
        tank_dir = "LEFT"
    if keys[pygame.K_RIGHT]:
        next_x += tank_speed
        tank_dir = "RIGHT"
    if keys[pygame.K_UP]:
        next_y -= tank_speed
        tank_dir = "UP"
    if keys[pygame.K_DOWN]:
        next_y += tank_speed
        tank_dir = "DOWN"

    # Prevent tank from moving off-screen
    next_x = max(0, min(next_x, 800 - tank_width))
    next_y = max(0, min(next_y, 600 - tank_height))

    # Check collision with walls
    future_rect = tank_rect(next_x, next_y)
    if not any(future_rect.colliderect(wall) for wall in walls):
        tank_x, tank_y = next_x, next_y

    screen.fill((0, 0, 0))  # Clear screen with black

    # Draw walls
    for wall in walls:
        pygame.draw.rect(screen, wall_color, wall)

    # Draw tank body
    pygame.draw.rect(screen, tank_color, (tank_x, tank_y, tank_width, tank_height))
    # Draw tank barrel
    if tank_dir == "UP":
        pygame.draw.rect(screen, (0, 200, 0), (tank_x + tank_width // 2 - 4, tank_y - 15, 8, 15))
    elif tank_dir == "DOWN":
        pygame.draw.rect(screen, (0, 200, 0), (tank_x + tank_width // 2 - 4, tank_y + tank_height, 8, 15))
    elif tank_dir == "LEFT":
        pygame.draw.rect(screen, (0, 200, 0), (tank_x - 15, tank_y + tank_height // 2 - 4, 15, 8))
    else:  # RIGHT
        pygame.draw.rect(screen, (0, 200, 0), (tank_x + tank_width, tank_y + tank_height // 2 - 4, 15, 8))

    # Move and draw bullets
    for bullet in bullets[:]:
        bullet[0] += bullet[2]
        bullet[1] += bullet[3]
        pygame.draw.rect(screen, bullet_color, (bullet[0], bullet[1], bullet_width, bullet_height))
        # Remove bullets off-screen
        if (bullet[0] < 0 or bullet[0] > 800 or bullet[1] < 0 or bullet[1] > 600):
            bullets.remove(bullet)

    pygame.display.flip()
    clock.tick(60)

pygame.quit()
sys.exit()