-- CampusFlow – Unified Digital Request and Approval Portal
-- MySQL 8 schema.  Run:  mysql -u root -p < sql/schema.sql
-- (Sample data is loaded by `python seed.py` because passwords must be hashed.)

CREATE DATABASE IF NOT EXISTS campusflow CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE campusflow;

CREATE TABLE departments (
  id INT AUTO_INCREMENT PRIMARY KEY,
  code VARCHAR(10) NOT NULL UNIQUE,
  name VARCHAR(120) NOT NULL,
  is_active TINYINT(1) NOT NULL DEFAULT 1
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE roles (
  id INT AUTO_INCREMENT PRIMARY KEY,
  code VARCHAR(20) NOT NULL UNIQUE,
  name VARCHAR(50) NOT NULL,
  description VARCHAR(255)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE permissions (
  id INT AUTO_INCREMENT PRIMARY KEY,
  code VARCHAR(50) NOT NULL UNIQUE,
  description VARCHAR(255) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE role_permissions (
  role_id INT NOT NULL,
  permission_id INT NOT NULL,
  PRIMARY KEY (role_id, permission_id),
  FOREIGN KEY (role_id) REFERENCES roles(id) ON DELETE CASCADE,
  FOREIGN KEY (permission_id) REFERENCES permissions(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE users (
  id INT AUTO_INCREMENT PRIMARY KEY,
  college_id VARCHAR(30) NOT NULL UNIQUE,
  email VARCHAR(120) NOT NULL UNIQUE,
  full_name VARCHAR(120) NOT NULL,
  password_hash VARCHAR(255) NOT NULL,
  role_id INT NOT NULL,
  department_id INT NULL,
  advisor_id INT NULL,
  designation VARCHAR(80),
  phone VARCHAR(20),
  is_active TINYINT(1) NOT NULL DEFAULT 1,
  failed_attempts INT NOT NULL DEFAULT 0,
  locked_until VARCHAR(19) NULL,
  last_login VARCHAR(19) NULL,
  created_at VARCHAR(19) NOT NULL,
  FOREIGN KEY (role_id) REFERENCES roles(id),
  FOREIGN KEY (department_id) REFERENCES departments(id),
  FOREIGN KEY (advisor_id) REFERENCES users(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE request_types (
  id INT AUTO_INCREMENT PRIMARY KEY,
  code VARCHAR(20) NOT NULL UNIQUE,
  name VARCHAR(60) NOT NULL,
  description VARCHAR(255),
  icon VARCHAR(40) NOT NULL DEFAULT 'bi-file-earmark-text',
  is_active TINYINT(1) NOT NULL DEFAULT 1
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- One row per step of the approval pipeline for (request type, requester role).
-- step_role: faculty (assigned advisor) | hod | principal.
-- min_days : the step applies only when the request spans at least this many days (0 = always).
CREATE TABLE workflow_rules (
  id INT AUTO_INCREMENT PRIMARY KEY,
  request_type_id INT NOT NULL,
  requester_role VARCHAR(20) NOT NULL,
  step_order INT NOT NULL,
  step_role VARCHAR(20) NOT NULL,
  min_days INT NOT NULL DEFAULT 0,
  FOREIGN KEY (request_type_id) REFERENCES request_types(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE requests (
  id INT AUTO_INCREMENT PRIMARY KEY,
  request_uid VARCHAR(20) UNIQUE,
  requester_id INT NOT NULL,
  department_id INT NULL,
  request_type_id INT NOT NULL,
  title VARCHAR(150) NOT NULL,
  reason VARCHAR(255) NOT NULL,
  description TEXT,
  start_date VARCHAR(10) NULL,
  end_date VARCHAR(10) NULL,
  days INT NOT NULL DEFAULT 0,
  extra TEXT,
  is_confidential TINYINT(1) NOT NULL DEFAULT 0,
  status VARCHAR(20) NOT NULL DEFAULT 'pending',
  current_step INT NOT NULL DEFAULT 0,
  created_at VARCHAR(19) NOT NULL,
  updated_at VARCHAR(19) NOT NULL,
  completed_at VARCHAR(19) NULL,
  FOREIGN KEY (requester_id) REFERENCES users(id),
  FOREIGN KEY (department_id) REFERENCES departments(id),
  FOREIGN KEY (request_type_id) REFERENCES request_types(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Snapshot of the pipeline for each request (so later rule edits never change history).
-- status: waiting | pending | approved | rejected | returned | skipped
CREATE TABLE request_steps (
  id INT AUTO_INCREMENT PRIMARY KEY,
  request_id INT NOT NULL,
  step_order INT NOT NULL,
  step_role VARCHAR(20) NOT NULL,
  assigned_user_id INT NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'waiting',
  FOREIGN KEY (request_id) REFERENCES requests(id) ON DELETE CASCADE,
  FOREIGN KEY (assigned_user_id) REFERENCES users(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE approval_history (
  id INT AUTO_INCREMENT PRIMARY KEY,
  request_id INT NOT NULL,
  step_id INT NULL,
  actor_id INT NOT NULL,
  action VARCHAR(20) NOT NULL,
  remark TEXT,
  from_status VARCHAR(20),
  to_status VARCHAR(20),
  created_at VARCHAR(19) NOT NULL,
  FOREIGN KEY (request_id) REFERENCES requests(id) ON DELETE CASCADE,
  FOREIGN KEY (actor_id) REFERENCES users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE attachments (
  id INT AUTO_INCREMENT PRIMARY KEY,
  request_id INT NOT NULL,
  original_name VARCHAR(255) NOT NULL,
  stored_name VARCHAR(80) NOT NULL UNIQUE,
  mime_type VARCHAR(100) NOT NULL,
  size_bytes INT NOT NULL,
  uploaded_by INT NOT NULL,
  created_at VARCHAR(19) NOT NULL,
  FOREIGN KEY (request_id) REFERENCES requests(id) ON DELETE CASCADE,
  FOREIGN KEY (uploaded_by) REFERENCES users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE notifications (
  id INT AUTO_INCREMENT PRIMARY KEY,
  user_id INT NOT NULL,
  request_id INT NULL,
  title VARCHAR(150) NOT NULL,
  message VARCHAR(255) NOT NULL,
  is_read TINYINT(1) NOT NULL DEFAULT 0,
  created_at VARCHAR(19) NOT NULL,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY (request_id) REFERENCES requests(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE audit_logs (
  id INT AUTO_INCREMENT PRIMARY KEY,
  user_id INT NULL,
  action VARCHAR(40) NOT NULL,
  entity VARCHAR(40),
  entity_id VARCHAR(40),
  details VARCHAR(500),
  ip_address VARCHAR(45),
  created_at VARCHAR(19) NOT NULL,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE INDEX idx_users_role ON users(role_id);
CREATE INDEX idx_users_dept ON users(department_id);
CREATE INDEX idx_req_requester ON requests(requester_id);
CREATE INDEX idx_req_dept ON requests(department_id);
CREATE INDEX idx_req_status ON requests(status);
CREATE INDEX idx_steps_req ON request_steps(request_id);
CREATE INDEX idx_steps_assigned ON request_steps(assigned_user_id, status);
CREATE INDEX idx_hist_req ON approval_history(request_id);
CREATE INDEX idx_notif_user ON notifications(user_id, is_read);
CREATE INDEX idx_audit_created ON audit_logs(created_at);
CREATE INDEX idx_rules_type ON workflow_rules(request_type_id, requester_role);
