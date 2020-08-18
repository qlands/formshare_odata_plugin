CREATE TABLE IF NOT EXISTS `odatauser` (
  `user_name` VARCHAR(120) NOT NULL,
  `user_password` TEXT NULL COMMENT 'Use Base64 for now',
  PRIMARY KEY (`user_name`))
ENGINE = InnoDB
DEFAULT CHARACTER SET = utf8;

CREATE TABLE IF NOT EXISTS `odatagroup` (
  `group_id` VARCHAR(12) NOT NULL,
  `group_name` VARCHAR(120) NULL,
  PRIMARY KEY (`group_id`))
ENGINE = InnoDB
DEFAULT CHARACTER SET = utf8;

CREATE TABLE IF NOT EXISTS `odatauseraccess` (
  `user_name` VARCHAR(120) NOT NULL,
  `table_name` VARCHAR(120) NOT NULL COMMENT 'A table in the database for example \"rpt_usage_msel_market\"',
  `allow_select` INT(1) NULL DEFAULT 1,
  `allow_insert` INT(1) NULL DEFAULT 0,
  `allow_update` INT(1) NULL DEFAULT 0,
  `allow_delete` INT(1) NULL DEFAULT 0,
  PRIMARY KEY (`user_name`, `table_name`),
  CONSTRAINT `fk_useraccess_odatauser1`
    FOREIGN KEY (`user_name`)
    REFERENCES `odatauser` (`user_name`)
    ON DELETE CASCADE
    ON UPDATE NO ACTION)
ENGINE = InnoDB
DEFAULT CHARACTER SET = utf8;

CREATE TABLE IF NOT EXISTS `odatagroupaccess` (
  `group_id` VARCHAR(12) NOT NULL,
  `table_name` VARCHAR(120) NOT NULL COMMENT 'A table in the database for example \"rpt_usage_msel_market\"',
  `allow_select` INT(1) NULL DEFAULT 1,
  `allow_insert` INT(1) NULL DEFAULT 0,
  `allow_update` INT(1) NULL DEFAULT 0,
  `allow_delete` INT(1) NULL DEFAULT 0,
  PRIMARY KEY (`group_id`, `table_name`),
  CONSTRAINT `fk_odatagroupaccess_odatagroup1`
    FOREIGN KEY (`group_id`)
    REFERENCES `odatagroup` (`group_id`)
    ON DELETE CASCADE
    ON UPDATE NO ACTION)
ENGINE = InnoDB
DEFAULT CHARACTER SET = utf8;

CREATE TABLE IF NOT EXISTS `odatagroupuser` (
  `group_id` VARCHAR(12) NOT NULL,
  `user_name` VARCHAR(120) NOT NULL,
  `join_date` DATETIME NULL,
  PRIMARY KEY (`group_id`, `user_name`),
  INDEX `fk_odatagroupuser_odatauser1_idx` (`user_name` ASC),
  CONSTRAINT `fk_odatagroupuser_odatagroup1`
    FOREIGN KEY (`group_id`)
    REFERENCES `odatagroup` (`group_id`)
    ON DELETE CASCADE
    ON UPDATE NO ACTION,
  CONSTRAINT `fk_odatagroupuser_odatauser1`
    FOREIGN KEY (`user_name`)
    REFERENCES `odatauser` (`user_name`)
    ON DELETE CASCADE
    ON UPDATE NO ACTION)
ENGINE = InnoDB
DEFAULT CHARACTER SET = utf8;
